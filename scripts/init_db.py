from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql

from app.config import settings

SQL_DIR = settings.base_dir / "sql"
DEMO_DB = "learning_space"
TEST_DB = "learning_space_test"


def admin_connect():
    return psycopg.connect(settings.admin_database_url, autocommit=True)


def db_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
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


def initialize_database(name: str, reset: bool) -> dict[str, object]:
    with admin_connect() as admin:
        if reset and db_exists(admin, name):
            drop_database(admin, name)
        if not db_exists(admin, name):
            create_database(admin, name)

    conninfo = settings.admin_database_url.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(conninfo, autocommit=True) as conn:
        if reset or not schema_exists(conn):
            apply_file(conn, SQL_DIR / "schema.sql")
        apply_file(conn, SQL_DIR / "views.sql")
        apply_file(conn, SQL_DIR / "roles.sql")

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
    return {"database": name, "tables": tables, "views": views}


def verify_roles(db_name: str) -> dict[str, object]:
    base = settings.admin_database_url.rsplit("/", 1)[0] + "/" + db_name
    app_url = settings.test_database_url.rsplit("/", 1)[0] + "/" + db_name
    t2s_url = settings.t2s_database_url.rsplit("/", 1)[0] + "/" + db_name

    checks: dict[str, object] = {}
    with psycopg.connect(app_url, autocommit=True) as conn:
        checks["ls_app_connects"] = conn.execute("SELECT 1").fetchone()[0] == 1

    with psycopg.connect(t2s_url, autocommit=True) as conn:
        checks["t2s_view_select"] = (
            conn.execute("SELECT count(*) FROM t2s.v_my_courses").fetchone()[0] == 0
        )
        try:
            conn.execute("SELECT * FROM app.users LIMIT 1")
        except psycopg.errors.InsufficientPrivilege:
            checks["t2s_blocked_from_app"] = True
        else:
            checks["t2s_blocked_from_app"] = False
        try:
            conn.execute("CREATE TABLE public._t2s_should_fail (x int)")
        except psycopg.errors.ReadOnlySqlTransaction:
            checks["t2s_read_only"] = True
        except psycopg.errors.InsufficientPrivilege:
            checks["t2s_read_only"] = True
        else:
            checks["t2s_read_only"] = False

    with psycopg.connect(base, autocommit=True) as conn:
        checks["schema_tables"] = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'app'"
        ).fetchone()[0]
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化学习空间数据库")
    parser.add_argument("--reset", action="store_true", help="删除并重建演示库和测试库")
    parser.add_argument("--skip-test", action="store_true", help="不初始化测试库")
    args = parser.parse_args()

    targets = [DEMO_DB] if args.skip_test else [DEMO_DB, TEST_DB]
    results = []
    for name in targets:
        try:
            results.append(initialize_database(name, args.reset))
        except Exception as exc:
            print(f"[FAIL] {name}: {exc}", file=sys.stderr)
            return 1

    for result in results:
        print(f"[OK] {result['database']}: {len(result['tables'])} tables, {len(result['views'])} views")
        print("     tables:", ", ".join(result["tables"]))

    checks = verify_roles(TEST_DB if not args.skip_test else DEMO_DB)
    for key, value in checks.items():
        print(f"[CHECK] {key}: {value}")
    if not all(
        checks.get(k) is True
        for k in ("ls_app_connects", "t2s_view_select", "t2s_blocked_from_app", "t2s_read_only")
    ):
        print("[FAIL] role verification failed", file=sys.stderr)
        return 1
    print("[DONE] database initialization complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
