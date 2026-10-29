from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from app.bootstrap import DEFAULT_DATABASE, TEST_DATABASE, initialize_database
from app.config import settings


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

    targets = (
        [DEFAULT_DATABASE]
        if args.skip_test
        else [DEFAULT_DATABASE, TEST_DATABASE]
    )
    results = []
    for name in targets:
        try:
            results.append(initialize_database(name, args.reset))
        except Exception as exc:
            print(f"[FAIL] {name}: {exc}", file=sys.stderr)
            return 1

    for result in results:
        tables = result["tables"]
        views = result["views"]
        migrations = result["migrations"]
        assert isinstance(tables, list) and isinstance(views, list)
        assert isinstance(migrations, list)
        print(f"[OK] {result['database']}: {len(tables)} tables, {len(views)} views")
        print("     tables:", ", ".join(tables))
        if migrations:
            print("     migrations:", ", ".join(migrations))
        else:
            print("     migrations: up to date")

    checks = verify_roles(TEST_DATABASE if not args.skip_test else DEFAULT_DATABASE)
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
