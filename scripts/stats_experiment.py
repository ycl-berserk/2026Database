"""在演示库上运行物化视图统计与实时聚合的对比实验（全部回滚）。"""

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
from app.services.stats_experiment import run_experiment


def admin_url(database: str) -> str:
    info = conninfo_to_dict(settings.admin_database_url)
    info["dbname"] = database
    return make_conninfo(**info)


def main() -> int:
    parser = argparse.ArgumentParser(description="物化视图统计与实时聚合对比实验")
    parser.add_argument(
        "--database",
        default=settings.database_url.rsplit("/", 1)[-1],
        help="演示数据库名，默认取 DATABASE_URL 的库名",
    )
    parser.add_argument("--user", default="alice", help="实验数据归属用户，默认 alice")
    parser.add_argument("--runs", type=int, default=15, help="每种查询的计时轮数")
    parser.add_argument(
        "--json",
        default=str(ROOT / "data" / "verification" / "stats-experiment.json"),
        help="JSON 输出路径",
    )
    args = parser.parse_args()

    try:
        conn = psycopg.connect(
            admin_url(args.database), autocommit=True, row_factory=dict_row
        )
    except psycopg.OperationalError as exc:
        print(f"[FAIL] 无法连接数据库：{exc}", file=sys.stderr)
        return 2

    with conn:
        row = conn.execute(
            "SELECT id FROM app.users WHERE username = %s", (args.user,)
        ).fetchone()
        if row is None:
            print(f"[FAIL] 缺少用户：{args.user}", file=sys.stderr)
            return 2
        result = run_experiment(conn, user_id=row["id"], runs=max(1, args.runs))

    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    timings = result["timings_ms"]
    print(f"数据库：{result['database']}；课程行：{result['rows']['live']}")
    print(
        "中位耗时：实时 "
        f"{timings['live_median']} ms，物化视图 {timings['materialized_median']} ms，"
        f"刷新 {timings['refresh']} ms"
    )
    if timings["speedup"]:
        print(f"读取加速：{timings['speedup']}x")
    print(
        "刷新语义：新增数据后陈旧 "
        f"{'是' if result['stale_detected'] else '否'}，刷新后一致 "
        f"{'是' if result['fresh_after_refresh'] else '否'}"
    )
    print(f"清理：{result['cleanup']}")
    print(f"结论：{'结果一致' if result['consistent'] else '结果不一致'}")
    print(f"报告：{output}")

    cleanup_ok = all(result["cleanup"].values())
    ok = (
        result["consistent"]
        and result["stale_detected"]
        and result["fresh_after_refresh"]
        and cleanup_ok
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
