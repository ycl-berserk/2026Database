"""数据库备份与恢复：调用 pg_dump / pg_restore，默认只允许恢复到 *_test 库。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.backup_service import (
    assert_restore_target,
    build_pg_dump_command,
    build_pg_restore_command,
    connection_params,
    dump_filename,
    with_target_database,
)


def _run(command: list[str], url: str, tool: str) -> None:
    env = dict(os.environ)
    env["PGPASSWORD"] = connection_params(url)["password"]
    try:
        completed = subprocess.run(command, env=env, check=False)
    except FileNotFoundError:
        raise SystemExit(
            f"未找到 {tool}。请安装 PostgreSQL 客户端，"
            "或用 docker exec 在容器内执行同样的命令。"
        ) from None
    if completed.returncode != 0:
        raise SystemExit(f"{tool} 执行失败，退出码 {completed.returncode}")


def _tool(name: str, pg_bin: str | None) -> str:
    if pg_bin:
        executable = Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))
        if executable.exists():
            return str(executable)
        raise SystemExit(f"未在 {pg_bin} 找到 {executable.name}")
    found = shutil.which(name)
    if found is None:
        raise SystemExit(
            f"未找到 {name}。请安装 PostgreSQL 客户端，"
            "或通过 --pg-bin 指定内置 bin 目录。"
        )
    return found


def _prune(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    dumps = sorted(
        directory.glob("learning-space-*.dump"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in dumps[keep:]:
        stale.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="学习空间数据库备份 / 恢复")
    parser.add_argument("--output-dir", default="data/backups")
    parser.add_argument("--database", help="目标库名，默认取 DATABASE_URL")
    parser.add_argument("--admin-url", help="管理连接串，默认取 ADMIN_DATABASE_URL")
    parser.add_argument("--keep", type=int, default=10, help="保留最近 N 份备份")
    parser.add_argument("--restore", metavar="DUMP", help="从 .dump 恢复")
    parser.add_argument("--force", action="store_true", help="允许恢复到非 *_test 库")
    parser.add_argument("--pg-bin", help="PostgreSQL bin 目录（内置 pg_dump/pg_restore）")
    args = parser.parse_args(argv)

    target = args.database or connection_params(settings.database_url)["dbname"]
    admin_url = args.admin_url or settings.admin_database_url
    url = with_target_database(admin_url, target)

    if args.restore:
        try:
            assert_restore_target(target, force=args.force)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        executable = _tool("pg_restore", args.pg_bin)
        _run(build_pg_restore_command(url, args.restore, executable), url, "pg_restore")
        print(f"[OK] 已恢复到 {target}：{args.restore}")
        return 0

    executable = _tool("pg_dump", args.pg_bin)
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / dump_filename()
    _run(build_pg_dump_command(url, output, executable), url, "pg_dump")
    _prune(directory, args.keep)
    size = output.stat().st_size
    print(f"[OK] 备份完成：{output}（{size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
