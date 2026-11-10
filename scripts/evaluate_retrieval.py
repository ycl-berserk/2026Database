"""检索召回评测：对比词法检索与 pgvector 混合检索的 Recall@K 与 MRR。"""

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
from app.services import retrieval_eval_service


def _print_summary(label: str, summary: dict | None) -> None:
    if summary is None:
        print(f"{label}：无可用结果")
        return
    print(
        f"{label}：Recall@k {summary['recall_at_k'] * 100:.1f}% "
        f"MRR {summary['mrr']:.3f}，命中 {summary['hits']}/{summary['cases']} 个用例"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="alice", help="评测使用的用户名")
    parser.add_argument("--k", type=int, default=10, help="Recall@K 与 MRR 的截断位置")
    parser.add_argument(
        "--cases", default=str(ROOT / "data" / "retrieval_eval.json")
    )
    parser.add_argument(
        "--json",
        default=str(ROOT / "data" / "verification" / "retrieval_eval.json"),
    )
    args = parser.parse_args()

    cases = retrieval_eval_service.load_cases(args.cases)
    with psycopg.connect(
        settings.database_url, autocommit=True, row_factory=dict_row
    ) as conn:
        user = conn.execute(
            "SELECT id FROM app.users WHERE username = %s AND is_active",
            (args.user,),
        ).fetchone()
        if user is None:
            print(f"没有找到可用用户：{args.user}")
            return 1
        report = retrieval_eval_service.evaluate(conn, user["id"], cases, k=args.k)

    output = Path(args.json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"模型：{report['model']}；截断：Top-{report['k']}")
    _print_summary("词法检索", report["lexical"])
    _print_summary("混合检索", report["hybrid"])
    if report["skipped_missing"]:
        print(f"跳过 {report['skipped_missing']} 个用例：演示库中找不到目标会话")
    if report["hybrid"] is None:
        print("混合检索未启用或向量生成失败：请配置 EMBEDDING_API_KEY 后重跑。")
    print(f"报告：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
