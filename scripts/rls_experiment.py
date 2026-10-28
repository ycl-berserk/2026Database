"""在演示库上运行 RLS / 视图 / 应用层 WHERE 的隔离对比实验（全部回滚）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from app.config import settings
from app.services.rls_experiment import run_experiment


def admin_url(database: str) -> str:
    info = conninfo_to_dict(settings.admin_database_url)
    info["dbname"] = database
    return make_conninfo(**info)


def main() -> int:
    parser = argparse.ArgumentParser(description="RLS 与权限视图对比实验")
    parser.add_argument(
        "--database",
        default=settings.database_url.rsplit("/", 1)[-1],
        help="演示数据库名，默认取 DATABASE_URL 的库名",
    )
    parser.add_argument(
        "--users", default="alice,bob", help="逗号分隔的用户名，默认 alice,bob"
    )
    parser.add_argument(
        "--json",
        default=str(ROOT / "data" / "verification" / "rls-experiment.json"),
        help="JSON 输出路径",
    )
    args = parser.parse_args()

    usernames = [name.strip() for name in args.users.split(",") if name.strip()]
    if not usernames:
        print("[FAIL] 至少需要一个用户", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(
            admin_url(args.database), autocommit=True, row_factory=dict_row
        )
    except psycopg.OperationalError as exc:
        print(f"[FAIL] 无法连接数据库：{exc}", file=sys.stderr)
        return 2

    with conn:
        rows = conn.execute(
            "SELECT id, username FROM app.users WHERE username = ANY(%s)",
            (usernames,),
        ).fetchall()
        users = {row["username"]: row["id"] for row in rows}
        missing = [name for name in usernames if name not in users]
        if missing:
            print(f"[FAIL] 缺少用户：{', '.join(missing)}", file=sys.stderr)
            return 2

        result = run_experiment(conn, users)

    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = result["summary"]
    print(f"数据库：{result['database']}；用户：{', '.join(users)}")
    for label, counts in summary["row_counts"].items():
        print(
            f"  {label}: 期望 {counts['expected']} 行；"
            f"视图 {counts['view']}，WHERE {counts['where']}，RLS {counts['rls']}"
        )
    print("计划形状：")
    for mode, data in summary["modes"].items():
        barrier = "含 Subquery Scan" if data["subquery_scan_users"] else "无 Subquery Scan"
        print(
            f"  {mode}: {barrier}，Filter {data['filter_count_total']} 个，"
            f"执行 {data['execution_ms_total']} ms"
        )
    print(
        f"未设置 GUC：视图 {result['unset_guc']['view_rows']} 行，"
        f"RLS {result['unset_guc']['rls_rows']} 行"
    )
    print(f"清理：{result['cleanup']}")
    print(f"结论：{'三种方式结果一致' if summary['consistent'] else '结果不一致'}")
    print(f"报告：{output}")

    cleanup_ok = all(result["cleanup"].values())
    return 0 if summary["consistent"] and cleanup_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
