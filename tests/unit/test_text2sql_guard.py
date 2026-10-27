from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services import text2sql_service as svc


def test_accepts_select_and_with():
    assert svc.check_sql("SELECT code FROM v_my_courses") == "SELECT code FROM v_my_courses"
    sql = "WITH x AS (SELECT conversation_id FROM v_my_conversations) SELECT * FROM x"
    assert svc.check_sql(sql) == sql
    assert svc.check_sql("select * from t2s.v_my_messages") == "select * from t2s.v_my_messages"


def test_strips_trailing_semicolon_and_code_fence():
    assert svc.check_sql("SELECT 1;") == "SELECT 1"
    assert svc.check_sql("```sql\nSELECT code FROM v_my_courses\n```") == (
        "SELECT code FROM v_my_courses"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "SELECT 1; SELECT 2",
        "INSERT INTO v_my_courses VALUES (1)",
        "DELETE FROM v_my_courses",
        "DROP TABLE app.users",
        "SELECT * FROM v_my_courses -- comment",
        "SELECT 1 /* comment */",
        "SELECT * FROM app.users",
        "SELECT * FROM public.users",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT current_user",
        "SELECT current_setting('app.user_id')",
        "SELECT * FROM missing_view",
    ],
)
def test_rejects_unsafe_sql(sql):
    with pytest.raises(svc.GuardError):
        svc.check_sql(sql)


def test_rejects_unknown_cte_reference():
    sql = "WITH x AS (SELECT 1) SELECT * FROM y"
    with pytest.raises(svc.GuardError):
        svc.check_sql(sql)


def test_wrap_sql_enforces_row_cap():
    wrapped = svc.wrap_sql("SELECT code FROM v_my_courses")
    assert wrapped.startswith("SELECT * FROM (")
    assert wrapped.endswith(f") AS q LIMIT {settings.text2sql_row_limit + 1}")


@pytest.mark.parametrize(
    "question",
    [
        "数据库课程下有哪些会话？",
        "今天到期的复习项",
        "我的复习队列",
        "未解决的批注",
        "按类型统计批注",
        "标签使用情况",
        "每月的活跃情况",
        "提问与回答的比例",
        "最近的导入记录",
        "text2sql 查询记录",
        "最近的会话",
        "未归类的会话",
        "课程消息统计",
        "完全无法识别的问题",
    ],
)
def test_mock_sql_passes_guard(question):
    sql, explanation = svc._mock_sql(question)
    assert svc.check_sql(sql)
    assert explanation


def test_mock_sql_matches_question_intent():
    sql, _ = svc._mock_sql("今天到期的复习项")
    assert "v_my_review_items" in sql and "current_date" in sql
    sql, _ = svc._mock_sql("数据库课程有哪些会话？")
    assert "course_code = 'DB'" in sql


def test_provider_mode_falls_back_to_mock(monkeypatch):
    original_mode = settings.text2sql_mode
    original_key = settings.llm_api_key
    try:
        object.__setattr__(settings, "text2sql_mode", "auto")
        object.__setattr__(settings, "llm_api_key", "")
        assert svc.provider_mode() == "mock"

        object.__setattr__(settings, "text2sql_mode", "mock")
        object.__setattr__(settings, "llm_api_key", "key")
        assert svc.provider_mode() == "mock"

        object.__setattr__(settings, "text2sql_mode", "llm")
        assert svc.provider_mode() == "llm"
    finally:
        object.__setattr__(settings, "text2sql_mode", original_mode)
        object.__setattr__(settings, "llm_api_key", original_key)


def test_generate_sql_falls_back_when_llm_fails(monkeypatch):
    async def boom(_question: str):
        raise RuntimeError("network down")

    monkeypatch.setattr(svc, "provider_mode", lambda: "llm")
    monkeypatch.setattr(svc, "_llm_sql", boom)
    sql, explanation, mode = asyncio.run(svc.generate_sql("复习"))
    assert mode == "mock-fallback"
    assert "v_my_review_items" in sql
    assert "大模型调用失败" in explanation
