from __future__ import annotations

import asyncio

import psycopg
import pytest

from app.config import settings
from app.errors import ValidationError
from app.services import text2sql_service as svc


def _fake_generate(sql: str, explanation: str = "测试查询"):
    async def generate(_question: str):
        return sql, explanation, "mock"

    return generate


def test_execute_query_filters_by_current_user(t2s_conn, base):
    sql = "SELECT conversation_id, title FROM v_my_conversations ORDER BY conversation_id"
    alice = svc.execute_query(t2s_conn, sql, base["users"]["alice"])
    assert alice["columns"] == ["conversation_id", "title"]
    assert {row["title"] for row in alice["rows"]} == {
        "事务与隔离级别",
        "进程调度",
        "未归类的问题",
    }

    bob = svc.execute_query(t2s_conn, sql, base["users"]["bob"])
    assert {row["title"] for row in bob["rows"]} == {"事务与隔离级别", "进程调度"}

    admin = svc.execute_query(t2s_conn, sql, base["users"]["admin"])
    assert admin["rows"] == []


def test_reader_role_is_read_only_and_isolated(t2s_conn):
    timeout = t2s_conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"]
    read_only = t2s_conn.execute(
        "SHOW default_transaction_read_only"
    ).fetchone()["default_transaction_read_only"]
    assert timeout == "3s"
    assert read_only == "on"
    t2s_conn.rollback()

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        t2s_conn.execute("SELECT count(*) FROM app.users")
    t2s_conn.rollback()

    with pytest.raises(psycopg.Error):
        t2s_conn.execute("INSERT INTO app.users (username, password_hash, display_name) "
                         "VALUES ('evil', repeat('x', 40), 'evil')")
    t2s_conn.rollback()


def test_run_success_records_log(conn, t2s_conn, base, monkeypatch):
    monkeypatch.setattr(
        svc,
        "generate_sql",
        _fake_generate("SELECT title, message_count FROM v_my_conversations ORDER BY title"),
    )
    result = asyncio.run(
        svc.run("我的会话有哪些", base["users"]["alice"], t2s_conn, conn)
    )
    assert result["mode"] == "mock"
    assert result["columns"] == ["title", "message_count"]
    assert result["row_count"] == 3
    assert result["truncated"] is False
    assert {row["title"] for row in result["rows"]} == {
        "事务与隔离级别",
        "进程调度",
        "未归类的问题",
    }

    logs = svc.history(conn, base["users"]["alice"])
    assert logs[0]["status"] == "success"
    assert logs[0]["row_count"] == 3
    assert logs[0]["question"] == "我的会话有哪些"
    assert logs[0]["generated_sql"].startswith("SELECT title")


def test_run_rejects_malicious_sql_and_logs(conn, t2s_conn, base, monkeypatch):
    monkeypatch.setattr(svc, "generate_sql", _fake_generate("DROP TABLE app.users"))
    with pytest.raises(svc.GuardError):
        asyncio.run(svc.run("删掉用户表", base["users"]["alice"], t2s_conn, conn))

    log = svc.history(conn, base["users"]["alice"])[0]
    assert log["status"] == "rejected"
    assert "DROP TABLE" in log["generated_sql"]
    assert log["row_count"] is None

    users = conn.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"]
    assert users == 3


def test_run_logs_execution_errors(conn, t2s_conn, base, monkeypatch):
    monkeypatch.setattr(svc, "generate_sql", _fake_generate("SELECT 1 / 0"))
    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(svc.run("除以零", base["users"]["alice"], t2s_conn, conn))
    assert "查询执行失败" in excinfo.value.message

    log = svc.history(conn, base["users"]["alice"])[0]
    assert log["status"] == "error"
    assert log["generated_sql"] == "SELECT 1 / 0"


def test_row_limit_marks_truncated(conn, t2s_conn, base, monkeypatch):
    original = settings.text2sql_row_limit
    try:
        object.__setattr__(settings, "text2sql_row_limit", 2)
        monkeypatch.setattr(
            svc,
            "generate_sql",
            _fake_generate("SELECT conversation_id FROM v_my_conversations ORDER BY conversation_id"),
        )
        result = asyncio.run(
            svc.run("全部会话", base["users"]["alice"], t2s_conn, conn)
        )
        assert len(result["rows"]) == 2
        assert result["truncated"] is True
    finally:
        object.__setattr__(settings, "text2sql_row_limit", original)


def test_history_is_user_scoped(conn, base):
    svc.record_log(
        conn,
        user_id=base["users"]["alice"],
        question="alice 的问题",
        sql="SELECT 1",
        status="success",
        latency_ms=3,
        row_count=1,
    )
    svc.record_log(
        conn,
        user_id=base["users"]["bob"],
        question="bob 的问题",
        sql="SELECT 2",
        status="success",
        latency_ms=4,
        row_count=1,
    )
    alice_logs = svc.history(conn, base["users"]["alice"])
    bob_logs = svc.history(conn, base["users"]["bob"])
    assert [log["question"] for log in alice_logs] == ["alice 的问题"]
    assert [log["question"] for log in bob_logs] == ["bob 的问题"]
