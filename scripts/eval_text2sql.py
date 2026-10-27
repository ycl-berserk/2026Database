"""Text2SQL 金标准评测：执行准确率、SQL 合法率与越权拦截率。"""

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
from psycopg.rows import dict_row

from app.config import settings
from app.services import text2sql_eval_service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", default=str(ROOT / "data" / "text2sql_eval.json")
    )
    parser.add_argument(
        "--json",
        default=str(ROOT / "data" / "verification" / "text2sql_eval.json"),
    )
    parser.add_argument(
        "--provider",
        choices=("mock", "llm", "auto"),
        default="mock",
        help="mock 可离线复现；llm 使用 LLM_API_KEY；auto 跟随配置",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.5,
        help="执行准确率下限，低于该值返回非零退出码",
    )
    args = parser.parse_args()

    cases = text2sql_eval_service.load_cases(args.cases)
    with (
        psycopg.connect(
            settings.database_url, autocommit=True, row_factory=dict_row
        ) as main_conn,
        psycopg.connect(
            settings.t2s_database_url, autocommit=True, row_factory=dict_row
        ) as t2s_conn,
    ):
        report = text2sql_eval_service.evaluate(
            cases, main_conn, t2s_conn, provider=args.provider
        )

    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    questions = report["questions"]
    security = report["security"]
    print(f"provider={report['provider']} modes={report['modes']}")
    print(
        f"执行准确率：{questions['matched']}/{questions['total']} "
        f"({questions['accuracy'] * 100:.1f}%)"
    )
    print(
        f"SQL 合法率：金标准 {report['legality']['gold_rate'] * 100:.1f}% / "
        f"生成 {report['legality']['generated_rate'] * 100:.1f}%"
    )
    print(
        f"越权拦截率：{security['passed']}/{security['total']} "
        f"({security['rate'] * 100:.1f}%)"
    )
    print(f"平均生成+执行耗时：{questions['avg_latency_ms']} ms")
    print(f"报告：{output}")

    failed = [
        case
        for case in report["cases"]
        if not case["match"] and case["status"] != "ok"
    ]
    if failed:
        print("未通过（生成阶段）：")
        for case in failed:
            print(f"  {case['id']} {case['status']}: {case.get('error', '')}")
    mismatched = [
        case for case in report["cases"] if case["status"] == "ok" and not case["match"]
    ]
    if mismatched:
        print("结果不一致：")
        for case in mismatched:
            print(f"  {case['id']} {case['question']}")

    ok = (
        report["legality"]["gold_rate"] == 1.0
        and security["rate"] == 1.0
        and questions["accuracy"] >= args.min_accuracy
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
