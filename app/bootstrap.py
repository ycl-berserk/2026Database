"""建库与初始化：schema、迁移、视图、最小权限角色。

演示模式（scripts/init_db.py）与桌面模式（app/desktop.py）共用这一套顺序，
区别只在于是否创建测试库、是否把角色密码替换为随机值。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import psycopg
from psycopg import sql

from .config import settings
from .migrations import apply_pending, pending_migrations

DEFAULT_DATABASE = "learning_space"
TEST_DATABASE = "learning_space_test"
MANAGED_ROLES = ("ls_app", "t2s_reader")


def admin_connect(url: str | None = None):
    return psycopg.connect(url or settings.admin_database_url, autocommit=True)


def database_url_for(base_url: str, name: str) -> str:
    return base_url.rsplit("/", 1)[0] + "/" + name


def db_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
    ).fetchone()
    return row is not None


def drop_database(conn, name: str) -> None:
    conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    conn.execute(
        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
    )


def create_database(conn, name: str) -> None:
    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def apply_file(conn, path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    conn.execute(path.read_text(encoding="utf-8"))


def schema_exists(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'app'"
    ).fetchone()
    return row is not None


def set_role_passwords(conn, passwords: Mapping[str, str]) -> None:
    for role, password in passwords.items():
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )


def user_count(conn) -> int:
    row = conn.execute("SELECT count(*) AS total FROM app.users").fetchone()
    return int(row["total"] if isinstance(row, dict) else row[0])


def is_initialized(conn) -> bool:
    row = conn.execute(
        "SELECT to_regclass('app.users') IS NOT NULL AS ready"
    ).fetchone()
    ready = row["ready"] if isinstance(row, dict) else row[0]
    return bool(ready) and user_count(conn) > 0


def initialize_database(
    name: str,
    reset: bool,
    *,
    admin_url: str | None = None,
    sql_dir: Path | None = None,
    role_passwords: Mapping[str, str] | None = None,
    before_migrate: Callable[[list[str]], None] | None = None,
) -> dict[str, object]:
    base_url = admin_url or settings.admin_database_url
    scripts = sql_dir or settings.base_dir / "sql"

    with admin_connect(base_url) as admin:
        if reset and db_exists(admin, name):
            drop_database(admin, name)
        created = not db_exists(admin, name)
        if created:
            create_database(admin, name)

    pending: list[str] = []
    with psycopg.connect(database_url_for(base_url, name), autocommit=True) as conn:
        if reset or not schema_exists(conn):
            apply_file(conn, scripts / "schema.sql")
        else:
            pending = pending_migrations(conn)
            if pending and before_migrate is not None:
                before_migrate(pending)
        applied = apply_pending(conn)
        apply_file(conn, scripts / "views.sql")
        apply_file(conn, scripts / "roles.sql")
        if role_passwords:
            set_role_passwords(conn, role_passwords)

        tables = [
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'app' ORDER BY table_name"
            ).fetchall()
        ]
        views = [
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema = 't2s' ORDER BY table_name"
            ).fetchall()
        ]
    return {
        "database": name,
        "tables": tables,
        "views": views,
        "migrations": applied,
        "created": created,
        "pending_before": pending,
    }
