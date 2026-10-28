"""物化视图统计与实时聚合的对比实验。

实验在 `force_rollback` 事务内创建临时 schema 与 `mv_course_stats` 物化视图，
对比同一份“按课程聚合会话/消息/批注”的查询：

1. 实时聚合：每次从 `app` 基表连接并分组
2. 物化视图：读取预先计算好的结果

随后插入一条新会话/消息，验证物化视图的陈旧性与 `REFRESH` 后的新鲜度。
结束后回滚全部 DDL 与数据，不在数据库中留下实验对象。
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from typing import Any

from psycopg import Connection, sql
from psycopg.rows import tuple_row

from .rls_experiment import summarize_plan

COURSE_STATS_SQL = sql.SQL(
    """
    SELECT c.id,
           count(DISTINCT conv.id) AS conversation_count,
           count(DISTINCT m.id) AS message_count,
           count(DISTINCT ann.id) AS annotation_count,
           max(m.created_at) AS last_activity
      FROM app.courses c
      LEFT JOIN app.conversations conv ON conv.course_id = c.id
      LEFT JOIN app.messages m ON m.conversation_id = conv.id
      LEFT JOIN app.annotations ann ON ann.message_id = m.id
     GROUP BY c.id
     ORDER BY c.id
    """
)


def _fetch(conn: Connection, query: sql.SQL | sql.Composed) -> list[tuple]:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query)
        return cur.fetchall()


def _scalar(conn: Connection, query: sql.SQL | sql.Composed, params: tuple = ()) -> Any:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return row[0] if row else None


def _median_ms(conn: Connection, query: sql.SQL | sql.Composed, runs: int) -> float:
    timings: list[float] = []
    with conn.cursor(row_factory=tuple_row) as cur:
        for _ in range(runs):
            started = time.perf_counter()
            cur.execute(query)
            cur.fetchall()
            timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    return round(timings[len(timings) // 2], 3)


def _explain(conn: Connection, query: sql.SQL | sql.Composed) -> dict:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ") + query
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("EXPLAIN 未返回结果")
    return summarize_plan(row[0])


def _schema_exists(conn: Connection, name: str) -> bool:
    return (
        _scalar(
            conn,
            sql.SQL("SELECT 1 FROM pg_namespace WHERE nspname = %s"),
            (name,),
        )
        is not None
    )


def run_experiment(
    conn: Connection,
    *,
    user_id: int,
    schema_prefix: str = "stats_experiment",
    runs: int = 15,
) -> dict:
    """对比实时聚合与物化视图，结束后回滚全部实验对象。"""
    suffix = secrets.token_hex(4)
    schema_name = f"{schema_prefix}_{suffix}"
    schema = sql.Identifier(schema_name)
    mv = sql.SQL("{}.mv_course_stats").format(schema)
    mv_select = sql.SQL("SELECT * FROM {} ORDER BY id").format(mv)
    index = sql.Identifier(f"mv_course_stats_{suffix}_id")

    with conn.transaction(force_rollback=True):
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        conn.execute(
            sql.SQL("CREATE MATERIALIZED VIEW {} AS ").format(mv) + COURSE_STATS_SQL
        )
        conn.execute(
            sql.SQL("CREATE UNIQUE INDEX {} ON {} (id)").format(index, mv)
        )

        live_rows = _fetch(conn, COURSE_STATS_SQL)
        materialized_rows = _fetch(conn, mv_select)
        consistent = live_rows == materialized_rows

        live_plan = _explain(conn, COURSE_STATS_SQL)
        materialized_plan = _explain(conn, mv_select)
        live_ms = _median_ms(conn, COURSE_STATS_SQL, runs)
        materialized_ms = _median_ms(conn, mv_select, runs)

        started = time.perf_counter()
        conn.execute(sql.SQL("REFRESH MATERIALIZED VIEW {}").format(mv))
        refresh_ms = round((time.perf_counter() - started) * 1000, 3)

        source_id = _scalar(
            conn,
            sql.SQL(
                "SELECT id FROM app.sources WHERE owner_id = %s ORDER BY id LIMIT 1"
            ),
            (user_id,),
        )
        course_id = _scalar(
            conn,
            sql.SQL(
                "INSERT INTO app.courses (code, name, semester, created_by)"
                " VALUES (%s, %s, %s, %s) RETURNING id"
            ),
            (f"MV{suffix[:4].upper()}", "物化视图实验课程", "2026 春", user_id),
        )
        conversation_id = _scalar(
            conn,
            sql.SQL(
                "INSERT INTO app.conversations"
                " (source_id, course_id, external_id, title, started_at)"
                " VALUES (%s, %s, %s, %s, now()) RETURNING id"
            ),
            (source_id, course_id, f"mv-{suffix}", "物化视图实验会话"),
        )
        conn.execute(
            sql.SQL(
                "INSERT INTO app.messages (conversation_id, seq_no, sender, content)"
                " VALUES (%s, 1, 'user', '物化视图刷新实验消息')"
            ),
            (conversation_id,),
        )

        live_after_insert = _fetch(conn, COURSE_STATS_SQL)
        stale_rows = _fetch(conn, mv_select)
        stale_detected = live_after_insert != stale_rows

        conn.execute(sql.SQL("REFRESH MATERIALIZED VIEW {}").format(mv))
        fresh_rows = _fetch(conn, mv_select)
        fresh_after_refresh = live_after_insert == fresh_rows

    cleanup = {"schema_dropped": not _schema_exists(conn, schema_name)}
    speedup = (
        round(live_ms / materialized_ms, 2) if materialized_ms > 0 else None
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "database": conn.info.dbname,
        "experiment_schema": schema_name,
        "rows": {"live": len(live_rows), "materialized": len(materialized_rows)},
        "timings_ms": {
            "live_median": live_ms,
            "materialized_median": materialized_ms,
            "refresh": refresh_ms,
            "runs": runs,
            "speedup": speedup,
        },
        "plans": {"live": live_plan, "materialized": materialized_plan},
        "consistent": consistent,
        "stale_detected": stale_detected,
        "fresh_after_refresh": fresh_after_refresh,
        "cleanup": cleanup,
        "notes": [
            "实验对象（schema、物化视图、唯一索引、临时课程/会话/消息）全部位于 force_rollback 事务内，结束后自动回滚。",
            "物化视图保存的是全库按课程聚合值；线上视图仍按当前用户成员关系过滤课程。",
            "刷新方式为普通 REFRESH（事务内可用）；生产环境可改为 REFRESH MATERIALIZED VIEW CONCURRENTLY 并由定时任务触发。",
            "演示数据规模小，耗时以中位数展示，主要信号是查询计划是否消除多表连接与去重聚合。",
        ],
    }
