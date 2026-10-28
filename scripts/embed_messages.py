"""为可见消息建立向量索引；没有配置向量模型时直接退出。"""

from __future__ import annotations

import argparse
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
from app.services import embedding_service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="只处理指定用户名；默认处理所有学生")
    parser.add_argument("--limit", type=int, default=512, help="每个用户最多处理的条数")
    parser.add_argument("--batch", type=int, default=64, help="每批提交的消息数")
    args = parser.parse_args()

    if not embedding_service.enabled():
        print("未启用向量模型：请配置 EMBEDDING_API_KEY / EMBEDDING_BASE_URL，"
              "或设置 EMBEDDING_MODE=llm。检索会继续使用词法模式。")
        return 2

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        if args.user:
            rows = conn.execute(
                "SELECT id, username FROM app.users "
                "WHERE username = %s AND system_role = 'student'",
                (args.user,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, username FROM app.users "
                "WHERE system_role = 'student' AND is_active ORDER BY id"
            ).fetchall()
        if not rows:
            print("没有找到可处理的用户")
            return 1

        for user in rows:
            before = embedding_service.coverage(conn, user["id"])
            remaining = min(args.limit, embedding_service.pending_count(conn, user["id"]))
            done = 0
            while done < remaining:
                size = min(args.batch, remaining - done)
                result = embedding_service.backfill(conn, user["id"], limit=size)
                if not result["embedded"]:
                    break
                done += result["embedded"]
            after = embedding_service.coverage(conn, user["id"])
            print(
                f"{user['username']}: 本次 {done} 条，"
                f"覆盖 {after['embedded']}/{after['total']}（之前 {before['embedded']}/{before['total']}）"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
