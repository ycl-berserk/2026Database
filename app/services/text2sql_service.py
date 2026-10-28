from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import sqlglot
import sqlparse
from sqlglot import exp
from sqlglot.errors import ParseError

from ..config import settings
from ..errors import ValidationError

VIEWS = (
    "v_my_courses",
    "v_my_conversations",
    "v_my_messages",
    "v_my_tags",
    "v_my_conversation_tags",
    "v_my_annotations",
    "v_my_review_items",
    "v_my_course_stats",
    "v_my_monthly_activity",
    "v_my_tag_stats",
    "v_my_sender_stats",
    "v_my_import_stats",
    "v_my_t2s_stats",
    "v_my_tag_edges",
    "v_my_tag_prerequisites",
)

SCHEMA_DOC = """
只读视图（均在 t2s schema，可直接不带前缀引用）：
- v_my_courses(course_id, code, name, semester, instructor, description, my_role, conversation_count, message_count)
- v_my_conversations(conversation_id, title, external_id, course_id, course_code, course_name, source_platform, account_label, started_at, ended_at, is_archived, message_count, tag_names)
- v_my_messages(message_id, conversation_id, conversation_title, course_id, course_name, seq_no, sender, content, created_at)
- v_my_tags(tag_id, name, kind, color, conversation_count)
- v_my_conversation_tags(conversation_id, conversation_title, tag_id, tag_name, tag_kind, created_by, created_by_name, created_at)
- v_my_annotations(annotation_id, kind, status, content, created_at, updated_at, message_id, message_content, sender, conversation_id, conversation_title, course_name, author_name, is_mine)
- v_my_review_items(review_item_id, status, due_date, interval_days, review_count, last_reviewed_at, annotation_id, annotation_kind, annotation_content, message_id, conversation_id, conversation_title, course_name)
- v_my_course_stats(course_id, code, name, semester, conversation_count, message_count, annotation_count, last_activity)
- v_my_monthly_activity(month, message_count, conversation_count, annotation_count)
- v_my_tag_stats(tag_id, name, kind, conversation_count, message_count)
- v_my_sender_stats(course_name, sender, message_count)
- v_my_import_stats(batch_id, file_name, status, platform, summary, created_at, imported_at)
- v_my_t2s_stats(log_id, question, status, latency_ms, row_count, created_at)
- v_my_tag_edges(edge_id, prerequisite_tag_id, prerequisite_tag, tag_id, tag_name, created_at)
- v_my_tag_prerequisites(tag_id, tag_name, prerequisite_tag_id, prerequisite_tag, depth)

约束：
1. 只生成一条 SELECT 或 WITH 查询，不要写分号，不要注释。
2. 只能查询上面的视图，不能访问 app schema、系统表或函数。
3. 结果最多返回 100 行，请自行加 ORDER BY，必要时加 LIMIT。
4. 视图已经按当前用户过滤，不需要再写 user_id 条件。
5. 日期字段可直接与 current_date 比较；月份字段为 'YYYY-MM' 文本。
"""

FEW_SHOT = """
示例：
问：每个课程有多少会话和消息？
答：{"sql": "SELECT code, name, conversation_count, message_count FROM v_my_course_stats ORDER BY message_count DESC", "explanation": "按课程聚合会话数与消息数，从多到少排序。"}
问：哪些复习项今天到期？
答：{"sql": "SELECT annotation_content, course_name, due_date, status FROM v_my_review_items WHERE status <> 'mastered' AND due_date <= current_date ORDER BY due_date", "explanation": "筛选未掌握且到期日不晚于今天的复习项。"}
问：按类型统计我的批注数量。
答：{"sql": "SELECT kind, count(*) AS total FROM v_my_annotations GROUP BY kind ORDER BY total DESC", "explanation": "按批注类型分组计数。"}
"""

DENY_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|"
    r"vacuum|analyze|reindex|cluster|comment|merge|refresh|set|reset|show|listen|"
    r"notify|unlisten|prepare|execute|deallocate|lock|into)\b",
    re.IGNORECASE,
)
FORBIDDEN_TARGETS = re.compile(
    r"\b(pg_[a-z_]+|information_schema|app\.|public\.|dblink|lo_[a-z_]+|"
    r"current_setting|current_user|session_user)\b",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
QUOTED_IDENTIFIER = re.compile(r'"(?:""|[^"])*"')
ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Copy,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
    exp.Use,
    exp.Merge,
    exp.Into,
    exp.Lock,
)
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink")
FORBIDDEN_FUNCTIONS = {
    "current_setting",
    "set_config",
    "current_user",
    "session_user",
    "current_role",
    "current_database",
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "txid_current",
    "version",
    "inet_server_addr",
    "inet_server_port",
}


class GuardError(ValidationError):
    pass


class PreflightError(ValidationError):
    pass


def _clean_sql_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_single(text: str):
    try:
        parsed = sqlglot.parse(text, read="postgres")
    except ParseError as exc:
        raise GuardError(f"SQL 无法解析：{exc}") from exc
    parsed = [statement for statement in parsed if statement is not None]
    if len(parsed) != 1:
        raise GuardError("只允许执行一条 SQL 语句")
    return parsed[0]


def _mask_literals(text: str) -> str:
    return QUOTED_IDENTIFIER.sub('""', STRING_LITERAL.sub("''", text))


def _validate_ast(ast) -> None:
    if not isinstance(ast, ALLOWED_ROOTS):
        raise GuardError("只允许 SELECT 或 WITH 查询")
    for node in ast.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardError("SQL 包含不被允许的写入或管理语句")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in ast.find_all(exp.CTE)
        if cte.alias_or_name
    }
    view_names = {name.lower() for name in VIEWS}
    for table in ast.find_all(exp.Table):
        name = (table.name or "").lower()
        db = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        if not name:
            raise GuardError("SQL 不允许在 FROM 中调用表函数")
        if catalog:
            raise GuardError(f"SQL 访问了未授权的对象：{catalog}.{db}.{name}")
        if db in ("", "t2s") and name in view_names:
            continue
        if not db and name in cte_names:
            continue
        qualified = f"{db}.{name}" if db else name
        raise GuardError(f"SQL 访问了未授权的对象：{qualified}")

    for func in ast.find_all(exp.Func):
        name = (func.name or "").lower()
        if not name:
            continue
        if name.startswith(FORBIDDEN_FUNCTION_PREFIXES) or name in FORBIDDEN_FUNCTIONS:
            raise GuardError(f"SQL 调用了不允许的函数：{name}")


def check_sql(sql: str) -> str:
    text = _clean_sql_text(sql)
    if not text:
        raise GuardError("生成的 SQL 为空")
    if "--" in text or "/*" in text:
        raise GuardError("SQL 中不允许出现注释")
    statements = [part for part in sqlparse.split(text) if part.strip().strip(";").strip()]
    if len(statements) != 1:
        raise GuardError("只允许执行一条 SQL 语句")
    text = statements[0].strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()

    ast = _parse_single(text)
    _validate_ast(ast)

    masked = _mask_literals(text)
    if DENY_KEYWORDS.search(masked):
        raise GuardError("SQL 包含不被允许的关键字")
    if FORBIDDEN_TARGETS.search(masked):
        raise GuardError("SQL 访问了不允许的 schema 或系统对象")
    return text


def wrap_sql(sql: str) -> str:
    limit = settings.text2sql_row_limit + 1
    return f"SELECT * FROM (\n{sql}\n) AS q LIMIT {limit}"


def preflight_query(t2s_conn, sql: str, user_id: int) -> dict | None:
    if not settings.text2sql_preflight_enabled:
        return None
    with t2s_conn.transaction():
        t2s_conn.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{settings.text2sql_preflight_timeout_ms}ms",),
        )
        t2s_conn.execute(
            "SELECT set_config('app.user_id', %s, true)", (str(user_id),)
        )
        row = t2s_conn.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchone()
    payload = next(iter(row.values())) if row else None
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not payload:
        raise PreflightError("查询预检没有返回执行计划")
    root = payload[0].get("Plan") or {}
    total_cost = float(root.get("Total Cost") or 0)
    plan_rows = int(root.get("Plan Rows") or 0)
    if total_cost > settings.text2sql_max_cost:
        raise PreflightError(
            f"查询预估代价 {total_cost:.0f} 超过上限 {settings.text2sql_max_cost:.0f}，已拒绝执行"
        )
    if plan_rows > settings.text2sql_max_est_rows:
        raise PreflightError(
            f"查询预估行数 {plan_rows} 超过上限 {settings.text2sql_max_est_rows}，已拒绝执行"
        )
    return {"total_cost": round(total_cost, 2), "plan_rows": plan_rows}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def execute_query(t2s_conn, sql: str, user_id: int) -> dict:
    wrapped = wrap_sql(sql)
    with t2s_conn.transaction():
        t2s_conn.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{settings.text2sql_timeout_ms}ms",),
        )
        t2s_conn.execute(
            "SELECT set_config('app.user_id', %s, true)", (str(user_id),)
        )
        cursor = t2s_conn.execute(wrapped)
        raw_rows = cursor.fetchall()
        columns = [column.name for column in cursor.description or []]
    limit = settings.text2sql_row_limit
    truncated = len(raw_rows) > limit
    rows = [
        {column: _jsonable(row[column]) for column in columns}
        for row in raw_rows[:limit]
    ]
    return {"columns": columns, "rows": rows, "truncated": truncated}


def record_log(
    conn,
    *,
    user_id: int,
    question: str,
    sql: str | None,
    status: str,
    latency_ms: int,
    row_count: int | None = None,
    error_message: str | None = None,
) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO app.text2sql_log
                (user_id, question, generated_sql, status, error_message,
                 latency_ms, row_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                question[:500],
                sql,
                status,
                error_message[:1000] if error_message else None,
                latency_ms,
                row_count,
            ),
        )


def history(conn, user_id: int, limit: int = 10) -> list[dict]:
    return conn.execute(
        """
        SELECT id, question, generated_sql, status, error_message,
               latency_ms, row_count, created_at
          FROM app.text2sql_log
         WHERE user_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT %s
        """,
        (user_id, limit),
    ).fetchall()


def provider_mode() -> str:
    mode = (settings.text2sql_mode or "auto").lower()
    if mode == "mock":
        return "mock"
    if mode == "llm":
        return "llm"
    return "llm" if settings.llm_api_key else "mock"


def _mock_sql(question: str) -> tuple[str, str]:
    q = question.strip()
    lowered = q.lower()

    course_codes = {
        "数据库": "DB",
        "操作系统": "OS",
        "计算机网络": "NET",
        "网络": "NET",
        "机器学习": "ML",
        "数据结构": "DS",
    }
    for keyword, code in course_codes.items():
        if keyword in q and ("课程" in q or "会话" in q or "消息" in q):
            return (
                "SELECT title, message_count, started_at "
                "FROM v_my_conversations "
                f"WHERE course_code = '{code}' "
                "ORDER BY started_at DESC NULLS LAST LIMIT 20",
                f"列出课程 {code} 下最近 20 个会话及其消息数。",
            )

    if ("待复习" in q or "复习" in q) and ("到期" in q or "今天" in q):
        return (
            "SELECT annotation_content, annotation_kind, course_name, due_date, status "
            "FROM v_my_review_items "
            "WHERE status <> 'mastered' AND due_date <= current_date "
            "ORDER BY due_date",
            "筛选未掌握且到期日不晚于今天的复习项，逾期项会排在最前。",
        )
    if "复习" in q:
        return (
            "SELECT annotation_content, annotation_kind, course_name, due_date, status "
            "FROM v_my_review_items WHERE status <> 'mastered' "
            "ORDER BY due_date",
            "列出未掌握的复习项，按到期日排序。",
        )

    if "未解决" in q and "批注" in q:
        return (
            "SELECT kind, content, course_name, created_at "
            "FROM v_my_annotations WHERE status = 'open' "
            "ORDER BY created_at DESC LIMIT 20",
            "列出所有待解决批注，最近创建的在前。",
        )
    if "批注" in q:
        return (
            "SELECT kind, count(*) AS total FROM v_my_annotations "
            "GROUP BY kind ORDER BY total DESC",
            "按批注类型统计数量。",
        )

    if "先修" in q or "前置" in q:
        return (
            "SELECT tag_name, prerequisite_tag, depth FROM v_my_tag_prerequisites "
            "ORDER BY tag_name, depth",
            "递归列出每个标签的全部先修标签，depth 为最短先修距离。",
        )
    if "标签" in q:
        return (
            "SELECT name, conversation_count, message_count FROM v_my_tag_stats "
            "ORDER BY conversation_count DESC LIMIT 12",
            "按关联会话数列出使用最多的 12 个标签。",
        )
    if "每月" in q or "月度" in q or "活跃" in q:
        return (
            "SELECT month, message_count, conversation_count FROM v_my_monthly_activity "
            "ORDER BY month",
            "按月查看消息与会话数量，空月份已补零。",
        )
    if "提问" in q and ("回答" in q or "比例" in q):
        return (
            "SELECT course_name, sender, message_count FROM v_my_sender_stats "
            "ORDER BY course_name, sender",
            "按课程对比提问与回答的消息数量。",
        )
    if "导入" in q:
        return (
            "SELECT file_name, platform, status, created_at FROM v_my_import_stats "
            "ORDER BY created_at DESC LIMIT 10",
            "列出最近 10 次导入及其状态。",
        )
    if "text2sql" in lowered or "查询记录" in q:
        return (
            "SELECT question, status, latency_ms, row_count, created_at "
            "FROM v_my_t2s_stats ORDER BY created_at DESC LIMIT 10",
            "查看最近 10 次 Text2SQL 查询记录。",
        )
    if "最近" in q and "会话" in q:
        return (
            "SELECT title, course_code, message_count, started_at "
            "FROM v_my_conversations ORDER BY started_at DESC NULLS LAST LIMIT 10",
            "按开始时间列出最近 10 个会话。",
        )
    if "未归类" in q:
        return (
            "SELECT title, message_count, started_at FROM v_my_conversations "
            "WHERE course_id IS NULL ORDER BY started_at DESC NULLS LAST",
            "列出尚未归入课程的会话。",
        )
    if "课程" in q and ("消息" in q or "会话" in q):
        return (
            "SELECT code, name, conversation_count, message_count, annotation_count "
            "FROM v_my_course_stats ORDER BY message_count DESC",
            "按课程统计会话、消息与批注数量，消息多的排在前面。",
        )
    return (
        "SELECT code, name, conversation_count, message_count, annotation_count "
        "FROM v_my_course_stats ORDER BY message_count DESC",
        "本地规则未命中具体问题，先返回课程总览；配置大模型后可自由提问。",
    )


async def _llm_sql(question: str) -> tuple[str, str]:
    system = (
        "你是 PostgreSQL 查询生成器。根据用户问题生成只读 SQL。"
        "只输出 JSON 对象，字段为 sql 和 explanation，不要 Markdown 代码块。"
        + SCHEMA_DOC
        + FEW_SHOT
    )
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    sql = str(data.get("sql") or "")
    explanation = str(data.get("explanation") or "由大模型生成")
    if not sql:
        raise ValueError("模型未返回 sql 字段")
    return sql, explanation


async def generate_sql(question: str) -> tuple[str, str, str]:
    mode = provider_mode()
    if mode == "mock":
        sql, explanation = _mock_sql(question)
        return sql, explanation, "mock"
    try:
        sql, explanation = await _llm_sql(question)
        return sql, explanation, "llm"
    except Exception as exc:  # noqa: BLE001 - 回退到本地规则，保证离线可用
        sql, explanation = _mock_sql(question)
        return sql, f"{explanation}（大模型调用失败，已回退本地规则：{exc.__class__.__name__}）", "mock-fallback"


async def run(question: str, user_id: int, t2s_conn, main_conn) -> dict:
    started = time.perf_counter()
    sql = None
    try:
        sql, explanation, mode = await generate_sql(question)
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        record_log(
            main_conn,
            user_id=user_id,
            question=question,
            sql=None,
            status="error",
            latency_ms=latency,
            error_message=str(exc),
        )
        raise ValidationError(f"生成 SQL 失败：{exc}") from exc

    try:
        safe_sql = check_sql(sql)
    except GuardError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        record_log(
            main_conn,
            user_id=user_id,
            question=question,
            sql=sql,
            status="rejected",
            latency_ms=latency,
            error_message=exc.message,
        )
        raise

    try:
        plan = preflight_query(t2s_conn, safe_sql, user_id)
    except PreflightError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        record_log(
            main_conn,
            user_id=user_id,
            question=question,
            sql=safe_sql,
            status="rejected",
            latency_ms=latency,
            error_message=exc.message,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        record_log(
            main_conn,
            user_id=user_id,
            question=question,
            sql=safe_sql,
            status="error",
            latency_ms=latency,
            error_message=f"预检失败：{exc}",
        )
        raise ValidationError(f"查询预检失败：{exc.__class__.__name__}") from exc

    try:
        result = execute_query(t2s_conn, safe_sql, user_id)
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        record_log(
            main_conn,
            user_id=user_id,
            question=question,
            sql=safe_sql,
            status="error",
            latency_ms=latency,
            error_message=str(exc),
        )
        raise ValidationError(f"查询执行失败：{exc.__class__.__name__}") from exc

    latency = int((time.perf_counter() - started) * 1000)
    record_log(
        main_conn,
        user_id=user_id,
        question=question,
        sql=safe_sql,
        status="success",
        latency_ms=latency,
        row_count=len(result["rows"]),
    )
    return {
        "question": question,
        "sql": safe_sql,
        "explanation": explanation,
        "mode": mode,
        "latency_ms": latency,
        "truncated": result["truncated"],
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": len(result["rows"]),
        "plan": plan,
    }
