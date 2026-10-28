"""RLS 与 security_barrier 视图的隔离对比实验。

实验在 `force_rollback` 事务内创建临时 schema/角色、克隆最小表集，
分别用三种方式读取“当前用户可见的会话”：

1. `t2s.v_my_conversations`（现有 security_barrier 视图 + GUC）
2. 应用层显式 WHERE（当前服务层的做法）
3. 基表行级安全策略 RLS + GUC

最后回滚全部 DDL/数据，不在数据库中留下任何实验对象。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from psycopg import Connection, sql
from psycopg.rows import tuple_row

UID = sql.SQL("nullif(current_setting('app.user_id', true), '')::bigint")

PLAN_NODE_LIMIT = 6


def summarize_plan(payload: Any) -> dict:
    """把 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` 结果压缩成可比较的摘要。"""
    if isinstance(payload, str):
        payload = json.loads(payload)
    top = payload[0] if isinstance(payload, list) else payload
    plan = top["Plan"]

    nodes: list[dict] = []

    def walk(node: dict) -> None:
        nodes.append(node)
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(plan)
    node_types = sorted({node.get("Node Type", "") for node in nodes})
    filters = [node["Filter"] for node in nodes if "Filter" in node]
    return {
        "planning_ms": round(float(top.get("Planning Time", 0.0)), 3),
        "execution_ms": round(float(top.get("Execution Time", 0.0)), 3),
        "node_count": len(nodes),
        "node_types": node_types,
        "filter_count": len(filters),
        "filters": filters[:PLAN_NODE_LIMIT],
        "has_subquery_scan": any(
            node.get("Node Type") == "Subquery Scan" for node in nodes
        ),
        "has_subplan": any("SubPlan Name" in node for node in nodes),
        "actual_rows": int(plan.get("Actual Rows", 0)),
        "shared_hit_blocks": int(plan.get("Shared Hit Blocks", 0)),
        "shared_read_blocks": int(plan.get("Shared Read Blocks", 0)),
    }


def _predicate(schema: sql.Identifier, qualifier: str | None) -> sql.Composed:
    prefix = f"{qualifier}." if qualifier else ""
    return sql.SQL(
        "EXISTS (SELECT 1 FROM {schema}.sources s"
        " WHERE s.id = {prefix}source_id AND s.owner_id = {uid})"
        " OR ({prefix}course_id IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM {schema}.course_members cm"
        " WHERE cm.course_id = {prefix}course_id AND cm.user_id = {uid}))"
    ).format(
        schema=schema,
        prefix=sql.SQL(prefix),
        uid=UID,
    )


def _view_query() -> sql.Composed:
    return sql.SQL(
        "SELECT conversation_id, title, course_id"
        " FROM t2s.v_my_conversations ORDER BY conversation_id"
    )


def _plain_query(schema: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        "SELECT conv.id, conv.title, conv.course_id"
        " FROM {schema}.conversations_plain conv WHERE ({predicate}) ORDER BY conv.id"
    ).format(
        schema=schema,
        predicate=_predicate(schema, "conv"),
    )


def _rls_query(schema: sql.Identifier) -> sql.Composed:
    return sql.SQL(
        "SELECT conv.id, conv.title, conv.course_id"
        " FROM {schema}.conversations_rls conv ORDER BY conv.id"
    ).format(schema=schema)


def _expected_query(uid: int) -> tuple[sql.Composed, tuple]:
    query = sql.SQL(
        "SELECT conv.id FROM app.conversations conv"
        " WHERE EXISTS (SELECT 1 FROM app.sources s"
        " WHERE s.id = conv.source_id AND s.owner_id = %s)"
        " OR (conv.course_id IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM app.course_members cm"
        " WHERE cm.course_id = conv.course_id AND cm.user_id = %s))"
        " ORDER BY conv.id"
    )
    return query, (uid, uid)


def _fetch_ids(
    conn: Connection, query: sql.Composable, params: tuple = ()
) -> list[int]:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query, params)
        return [int(row[0]) for row in cur.fetchall()]


def _explain(conn: Connection, query: sql.Composable) -> dict:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ") + query
        )
        payload = cur.fetchone()[0]
    return summarize_plan(payload)


def _set_uid(conn: Connection, uid: int | None) -> None:
    conn.execute(
        "SELECT set_config('app.user_id', %s, true)",
        ("" if uid is None else str(uid),),
    )


def _set_role(conn: Connection, role: str | None) -> None:
    conn.execute("RESET ROLE")
    if role:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))


def _run_mode(conn: Connection, query: sql.Composable, role: str | None) -> dict:
    _set_role(conn, role)
    ids = _fetch_ids(conn, query)
    plan = _explain(conn, query)
    _set_role(conn, None)
    return {"ids": ids, "row_count": len(ids), "plan": plan}


def _catalog_exists(conn: Connection, kind: str, name: str) -> bool:
    if kind == "schema":
        row = conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)
        ).fetchone()
    return row is not None


def _mode_summary(users: dict, mode: str) -> dict:
    plans = [
        payload["modes"][mode]["plan"]
        for payload in users.values()
        if payload["modes"].get(mode)
    ]
    return {
        "users": len(plans),
        "execution_ms_total": round(
            sum(plan["execution_ms"] for plan in plans), 3
        ),
        "planning_ms_total": round(sum(plan["planning_ms"] for plan in plans), 3),
        "subquery_scan_users": sum(1 for plan in plans if plan["has_subquery_scan"]),
        "subplan_users": sum(1 for plan in plans if plan["has_subplan"]),
        "filter_count_total": sum(plan["filter_count"] for plan in plans),
        "node_types": sorted({node for plan in plans for node in plan["node_types"]}),
    }


def run_experiment(
    conn: Connection,
    users: Mapping[str, int],
    *,
    schema_prefix: str = "rls_experiment",
    role_prefix: str = "rls_probe",
) -> dict:
    """在同一事务内对比三种隔离方式，结束后全部回滚。"""
    if not users:
        raise ValueError("至少需要一个用户")
    suffix = secrets.token_hex(4)
    schema_name = f"{schema_prefix}_{suffix}"
    role_name = f"{role_prefix}_{suffix}"
    schema = sql.Identifier(schema_name)
    role = sql.Identifier(role_name)

    results: dict[str, dict] = {}

    with conn.transaction(force_rollback=True):
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        conn.execute(sql.SQL("CREATE ROLE {}").format(role))

        for table in ("sources", "course_members"):
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {schema}.{table} AS SELECT * FROM app.{table}"
                ).format(schema=schema, table=sql.Identifier(table))
            )
        conn.execute(
            sql.SQL(
                "CREATE TABLE {schema}.conversations_plain AS"
                " SELECT id, source_id, course_id, title FROM app.conversations"
            ).format(schema=schema)
        )
        conn.execute(
            sql.SQL(
                "CREATE TABLE {schema}.conversations_rls AS"
                " SELECT id, source_id, course_id, title FROM app.conversations"
            ).format(schema=schema)
        )

        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role)
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                schema, role
            )
        )

        policy = sql.SQL(
            "ALTER TABLE {schema}.conversations_rls ENABLE ROW LEVEL SECURITY"
        ).format(schema=schema)
        conn.execute(policy)
        conn.execute(
            sql.SQL(
                "ALTER TABLE {schema}.conversations_rls FORCE ROW LEVEL SECURITY"
            ).format(schema=schema)
        )
        conn.execute(
            sql.SQL(
                "CREATE POLICY conversation_visibility"
                " ON {schema}.conversations_rls FOR SELECT USING ({predicate})"
            ).format(
                schema=schema,
                predicate=_predicate(schema, "conversations_rls"),
            )
        )

        view_query = _view_query()
        plain_query = _plain_query(schema)
        rls_query = _rls_query(schema)

        for label, uid in users.items():
            expected_query, params = _expected_query(uid)
            expected_ids = _fetch_ids(conn, expected_query, params)

            _set_uid(conn, uid)
            modes = {
                "view": _run_mode(conn, view_query, "t2s_reader"),
                "where": _run_mode(conn, plain_query, role_name),
                "rls": _run_mode(conn, rls_query, role_name),
            }
            consistent = all(
                payload["ids"] == expected_ids for payload in modes.values()
            )
            results[label] = {
                "user_id": uid,
                "expected_ids": expected_ids,
                "modes": modes,
                "consistent": consistent,
            }

        _set_uid(conn, None)
        _set_role(conn, "t2s_reader")
        unset = {
            "view_rows": len(_fetch_ids(conn, view_query)),
        }
        _set_role(conn, role_name)
        unset["rls_rows"] = len(_fetch_ids(conn, rls_query))
        _set_role(conn, None)
        _set_uid(conn, None)

    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT current_user")
        session_user = cur.fetchone()[0]
    cleanup = {
        "schema_dropped": not _catalog_exists(conn, "schema", schema_name),
        "role_dropped": not _catalog_exists(conn, "role", role_name),
        "session_user_restored": session_user == conn.info.user,
    }

    modes = ("view", "where", "rls")
    consistent = all(item["consistent"] for item in results.values())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": conn.info.dbname,
        "experiment_objects": {"schema": schema_name, "role": role_name},
        "users": results,
        "unset_guc": unset,
        "summary": {
            "consistent": consistent,
            "users": len(results),
            "modes": {
                mode: _mode_summary(results, mode) for mode in modes
            },
            "row_counts": {
                label: {
                    "expected": len(item["expected_ids"]),
                    **{
                        mode: item["modes"][mode]["row_count"]
                        for mode in modes
                    },
                }
                for label, item in results.items()
            },
        },
        "cleanup": cleanup,
        "notes": [
            "实验对象（schema、角色、克隆表）全部位于 force_rollback 事务内，结束后自动回滚。",
            "探针角色为 NOLOGIN，且表启用了 FORCE ROW LEVEL SECURITY，避免表属主绕过策略。",
            "演示数据规模小，执行时间受噪声影响；以计划形状（Subquery Scan / Filter / SubPlan）为主要信号。",
            "现有 security_barrier 视图无需改表即可上线；RLS 方案把规则下推到基表，但策略子查询会出现在每个访问路径中。",
        ],
    }
