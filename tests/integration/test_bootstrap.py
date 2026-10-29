from __future__ import annotations

import uuid
from urllib.parse import quote, urlsplit

import psycopg
import pytest

from app.bootstrap import (
    admin_connect,
    apply_file,
    create_database,
    database_url_for,
    db_exists,
    drop_database,
    initialize_database,
    user_count,
)
from app.config import settings

pytestmark = pytest.mark.integration


def _db_name() -> str:
    return f"ls_bootstrap_{uuid.uuid4().hex[:8]}_test"


def _drop(name: str) -> None:
    with admin_connect() as conn:
        if db_exists(conn, name):
            drop_database(conn, name)


@pytest.fixture
def database():
    name = _db_name()
    try:
        yield name
    finally:
        _drop(name)


def _role_url(role: str, password: str, name: str) -> str:
    parts = urlsplit(settings.admin_database_url)
    return (
        f"postgresql://{role}:{quote(password, safe='')}"
        f"@{parts.hostname}:{parts.port or 5432}/{name}"
    )


def _create_bare_database(name: str) -> None:
    with admin_connect() as conn:
        create_database(conn, name)
    with psycopg.connect(
        database_url_for(settings.admin_database_url, name), autocommit=True
    ) as conn:
        apply_file(conn, settings.base_dir / "sql" / "schema.sql")


def test_initialize_creates_only_main_database_without_demo_users(database):
    name = database
    result = initialize_database(name, False)

    assert result["created"] is True
    assert "users" in result["tables"]
    assert "v_my_courses" in result["views"]
    assert result["pending_before"] == []

    with admin_connect() as conn:
        assert db_exists(conn, name) is True
        assert db_exists(conn, name + "_test") is False

    with psycopg.connect(
        database_url_for(settings.admin_database_url, name)
    ) as conn:
        assert user_count(conn) == 0

    again = initialize_database(name, False)
    assert again["created"] is False
    assert again["migrations"] == []


def test_bootstrap_backs_up_before_pending_migrations(database):
    name = database
    _create_bare_database(name)

    seen: list[list[str]] = []
    result = initialize_database(name, False, before_migrate=seen.append)

    assert seen, "存在待执行迁移时应先触发备份回调"
    assert result["pending_before"] == seen[0]
    assert result["pending_before"]
    assert result["migrations"]


def test_bootstrap_overrides_role_passwords(database):
    name = database
    app_pw = "App-" + uuid.uuid4().hex
    t2s_pw = "T2s-" + uuid.uuid4().hex
    try:
        initialize_database(
            name,
            False,
            role_passwords={"ls_app": app_pw, "t2s_reader": t2s_pw},
        )

        with psycopg.connect(_role_url("ls_app", app_pw, name), autocommit=True) as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM app.users").fetchone()[0] == 0

        with psycopg.connect(_role_url("t2s_reader", t2s_pw, name), autocommit=True) as conn:
            assert (
                conn.execute("SELECT count(*) FROM t2s.v_my_courses").fetchone()[0] == 0
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM app.users LIMIT 1")

        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(
                _role_url("ls_app", "wrong-password", name), connect_timeout=5
            )
    finally:
        with admin_connect() as conn:
            conn.execute("ALTER ROLE ls_app WITH PASSWORD 'ls_app_dev_pw'")
            conn.execute("ALTER ROLE t2s_reader WITH PASSWORD 't2s_dev_pw'")
