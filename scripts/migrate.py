from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import psycopg

from app.config import settings
from app.migrations import apply_pending, pending_migrations

DEMO_DB = "learning_space"
TEST_DB = "learning_space_test"


def database_url(name: str) -> str:
    return settings.admin_database_url.rsplit("/", 1)[0] + "/" + name


def main() -> int:
    parser = argparse.ArgumentParser(description="应用学习空间数据库迁移")
    parser.add_argument(
        "--database",
        action="append",
        dest="databases",
        help="目标数据库，可重复；默认演示库与测试库",
    )
    parser.add_argument("--check", action="store_true", help="只检查待执行迁移")
    args = parser.parse_args()

    targets = args.databases or [DEMO_DB, TEST_DB]
    has_pending = False
    for name in targets:
        with psycopg.connect(database_url(name), autocommit=True) as conn:
            if args.check:
                pending = pending_migrations(conn)
                if pending:
                    has_pending = True
                    print(f"[PENDING] {name}: {', '.join(pending)}")
                else:
                    print(f"[OK] {name}: 无待执行迁移")
                continue
            applied = apply_pending(conn)
            if applied:
                print(f"[OK] {name}: 已应用 {', '.join(applied)}")
            else:
                print(f"[OK] {name}: 已是最新")

    return 1 if has_pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
