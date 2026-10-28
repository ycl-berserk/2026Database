from __future__ import annotations

from psycopg import errors

PLAN_CHOICES: dict[str, dict[str, str]] = {
    "search_messages": {
        "label": "消息关键词检索",
        "description": "trgm / ILIKE 子串检索，观察是否走 GIN 索引。",
        "sql": (
            "SELECT m.id, m.conversation_id, m.seq_no "
            "FROM app.messages m "
            "WHERE m.content ILIKE '%隔离级别%' ESCAPE '\\'"
        ),
    },
    "list_conversations": {
        "label": "会话列表",
        "description": "按来源所有者过滤并按开始时间排序。",
        "sql": (
            "SELECT c.id, c.title, c.started_at "
            "FROM app.conversations c "
            "JOIN app.sources s ON s.id = c.source_id "
            "WHERE s.owner_id = 1 "
            "ORDER BY c.started_at DESC "
            "LIMIT 20"
        ),
    },
    "due_reviews": {
        "label": "到期复习",
        "description": "部分索引覆盖未掌握且已到期的复习项。",
        "sql": (
            "SELECT r.id, r.due_date, r.interval_days "
            "FROM app.review_items r "
            "WHERE r.user_id = 1 AND r.status <> 'mastered' "
            "  AND r.due_date <= current_date "
            "ORDER BY r.due_date, r.id "
            "LIMIT 20"
        ),
    },
    "course_rollup": {
        "label": "课程聚合",
        "description": "课程、会话、消息三级 LEFT JOIN 与 DISTINCT 计数。",
        "sql": (
            "SELECT c.id, c.code, "
            "       count(DISTINCT cv.id) AS conversation_count, "
            "       count(DISTINCT m.id) AS message_count "
            "FROM app.courses c "
            "LEFT JOIN app.conversations cv ON cv.course_id = c.id "
            "LEFT JOIN app.messages m ON m.conversation_id = cv.id "
            "GROUP BY c.id, c.code "
            "ORDER BY message_count DESC"
        ),
    },
}


def overview(conn) -> dict:
    return conn.execute(
        """
        SELECT d.datname,
               pg_database_size(d.datname) AS size_bytes,
               d.numbackends,
               current_setting('max_connections')::int AS max_connections,
               d.xact_commit,
               d.xact_rollback,
               d.blks_read,
               d.blks_hit,
               round(
                   100.0 * d.blks_hit / nullif(d.blks_hit + d.blks_read, 0), 2
               ) AS cache_hit_pct,
               d.tup_returned,
               d.tup_fetched,
               d.tup_inserted,
               d.tup_updated,
               d.tup_deleted,
               d.temp_bytes,
               d.deadlocks,
               d.stats_reset,
               pg_postmaster_start_time() AS started_at
          FROM pg_stat_database d
         WHERE d.datname = current_database()
        """
    ).fetchone()


def io_stats(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT backend_type,
               coalesce(sum(reads), 0) AS reads,
               coalesce(sum(writes), 0) AS writes,
               coalesce(sum(extends), 0) AS extends,
               coalesce(sum(hits), 0) AS hits,
               coalesce(sum(evictions), 0) AS evictions,
               coalesce(sum(fsyncs), 0) AS fsyncs
          FROM pg_stat_io
         GROUP BY backend_type
         ORDER BY sum(reads + writes) DESC, backend_type
        """
    ).fetchall()


def table_activity(conn, limit: int = 12) -> list[dict]:
    return conn.execute(
        """
        SELECT relname,
               coalesce(seq_scan, 0) AS seq_scan,
               coalesce(idx_scan, 0) AS idx_scan,
               n_live_tup,
               n_dead_tup,
               coalesce(n_tup_ins, 0) AS n_tup_ins,
               coalesce(n_tup_upd, 0) AS n_tup_upd,
               coalesce(n_tup_del, 0) AS n_tup_del,
               last_autovacuum,
               pg_total_relation_size(relid) AS size_bytes
          FROM pg_stat_user_tables
         WHERE schemaname = 'app'
         ORDER BY pg_total_relation_size(relid) DESC, relname
         LIMIT %s
        """,
        (max(1, min(limit, 30)),),
    ).fetchall()


def index_usage(conn, limit: int = 12) -> list[dict]:
    return conn.execute(
        """
        SELECT relname, indexrelname,
               coalesce(idx_scan, 0) AS idx_scan,
               coalesce(idx_tup_read, 0) AS idx_tup_read,
               coalesce(idx_tup_fetch, 0) AS idx_tup_fetch,
               pg_relation_size(indexrelid) AS size_bytes
          FROM pg_stat_user_indexes
         WHERE schemaname = 'app'
         ORDER BY idx_scan, pg_relation_size(indexrelid) DESC
         LIMIT %s
        """,
        (max(1, min(limit, 30)),),
    ).fetchall()


def slow_queries(conn, limit: int = 8) -> dict:
    try:
        with conn.transaction():
            rows = conn.execute(
                """
                SELECT left(regexp_replace(query, '\\s+', ' ', 'g'), 220) AS query,
                       calls,
                       round(total_exec_time::numeric, 1) AS total_ms,
                       round(mean_exec_time::numeric, 2) AS mean_ms,
                       rows,
                       round(
                           100.0 * shared_blks_hit
                           / nullif(shared_blks_hit + shared_blks_read, 0), 1
                       ) AS hit_pct
                  FROM pg_stat_statements
                 WHERE dbid = (
                         SELECT oid FROM pg_database
                          WHERE datname = current_database()
                       )
                   AND query NOT ILIKE '%%pg_stat_statements%%'
                   AND query NOT ILIKE '%%EXPLAIN%%'
                   AND query ~* '^\\s*(select|with|insert|update|delete)(\\s|$)'
                 ORDER BY mean_exec_time DESC NULLS LAST, calls DESC
                 LIMIT %s
                """,
                (max(1, min(limit, 30)),),
            ).fetchall()
    except (
        errors.UndefinedTable,
        errors.UndefinedFunction,
        errors.InsufficientPrivilege,
        errors.FeatureNotSupported,
        errors.ObjectNotInPrerequisiteState,
    ):
        return {
            "available": False,
            "reason": "pg_stat_statements 未加载或当前角色无权读取",
            "rows": [],
        }
    return {"available": True, "reason": None, "rows": rows}


def _flatten_plan(node: dict, depth: int, out: list[dict]) -> None:
    out.append(
        {
            "depth": depth,
            "node_type": node.get("Node Type") or "?",
            "relation": node.get("Relation Name") or node.get("CTE Name") or "",
            "alias": node.get("Alias") or "",
            "startup_cost": node.get("Startup Cost"),
            "total_cost": node.get("Total Cost"),
            "plan_rows": node.get("Plan Rows"),
            "plan_width": node.get("Plan Width"),
            "detail": node.get("Index Cond")
            or node.get("Filter")
            or node.get("Hash Cond")
            or node.get("Recheck Cond")
            or "",
        }
    )
    for child in node.get("Plans") or []:
        _flatten_plan(child, depth + 1, out)


def explain_plan(conn, key: str | None) -> dict:
    key = key if key in PLAN_CHOICES else next(iter(PLAN_CHOICES))
    choice = PLAN_CHOICES[key]
    row = conn.execute("EXPLAIN (FORMAT JSON) " + choice["sql"]).fetchone()
    plan = row["QUERY PLAN"][0]
    rows: list[dict] = []
    _flatten_plan(plan["Plan"], 0, rows)
    return {
        "key": key,
        "label": choice["label"],
        "description": choice["description"],
        "sql": choice["sql"],
        "planning_time": plan.get("Planning Time"),
        "rows": rows,
    }
