"""把 PostgreSQL、sidecar 与 SQL 资源汇总到 Tauri 的 resources 目录。

用法（Windows）::

    .venv\\Scripts\\python.exe packaging\\build.py
    .venv\\Scripts\\python.exe packaging\\build.py --skip-fetch --skip-pgvector

每个阶段都可以跳过；产物目录 ``desktop/src-tauri/resources`` 每次会重建。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
BUILD = PACKAGING / "build"
CACHE = PACKAGING / ".cache"
PG_ROOT = BUILD / "pgsql"
RAW_PG = CACHE / "raw-16.10" / "pgsql"
SIDECAR_DIST = BUILD / "sidecar"
RESOURCES = ROOT / "desktop" / "src-tauri" / "resources"
REPORT = BUILD / "size-report.json"

sys.path.insert(0, str(ROOT))


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("[run]", " ".join(str(part) for part in command))
    subprocess.run(command, cwd=cwd, check=True)


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def fetch_postgres(force: bool) -> int:
    if (PG_ROOT / "bin" / "postgres.exe").exists() and not force:
        print(f"[skip] PostgreSQL 已存在：{PG_ROOT}")
    else:
        _run(
            [
                sys.executable,
                str(PACKAGING / "postgres" / "fetch_trim.py"),
                "--stage",
                "all",
                *(["--force"] if force else []),
            ]
        )
    return _dir_size(PG_ROOT)


def build_pgvector() -> None:
    vector = PG_ROOT / "share" / "extension" / "vector.control"
    library = PG_ROOT / "lib" / "vector.dll"
    if vector.exists() and library.exists():
        print("[skip] pgvector 已安装")
        return
    if os.name != "nt":
        raise SystemExit("pgvector 构建脚本目前只支持 Windows（macOS 在 Phase 4 处理）")
    if not (RAW_PG / "include" / "postgresql" / "server" / "postgres.h").exists():
        raise SystemExit("缺少 EDB 头文件，请先运行 fetch_trim.py --stage all")
    _run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PACKAGING / "postgres" / "build_pgvector.ps1"),
            "-PgRoot",
            str(RAW_PG),
            "-InstallDir",
            str(PG_ROOT),
        ]
    )


def _sidecar_exe() -> Path:
    name = "ls-server.exe" if os.name == "nt" else "ls-server"
    return SIDECAR_DIST / "ls-server" / name


def build_sidecar(clean: bool) -> int:
    output = _sidecar_exe()
    if output.exists() and not clean:
        print(f"[skip] sidecar 已存在：{output}")
        return _dir_size(SIDECAR_DIST / "ls-server")

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(SIDECAR_DIST),
            "--workpath",
            str(CACHE / "pyinstaller"),
            str(PACKAGING / "sidecar" / "ls-server.spec"),
        ]
    )
    if not output.exists():
        raise SystemExit(f"sidecar 构建失败，未找到 {output}")
    return _dir_size(SIDECAR_DIST / "ls-server")


def stage_resources() -> dict[str, int]:
    sizes = {}
    for name, source in (
        ("pgsql", PG_ROOT),
        ("sidecar", SIDECAR_DIST / "ls-server"),
        ("sql", ROOT / "sql"),
    ):
        if not source.exists():
            raise SystemExit(f"缺少资源目录：{source}")
        target = RESOURCES / name
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        sizes[name] = _dir_size(target)
    (RESOURCES / "version.json").write_text(
        json.dumps(
            {
                "name": "Learning Space",
                "resources": sizes,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return sizes


def write_report(sizes: dict[str, int]) -> int:
    total = sum(sizes.values())
    report = {
        "platform": sys.platform,
        "bytes": sizes,
        "total_bytes": total,
        "total_mib": round(total / 1024 / 1024, 2),
        "budget_mib": 350,
        "within_budget": total <= 350 * 1024 * 1024,
        "nsis_target_mib": 120,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = [
        "### Desktop resources",
        "",
        "| 资源 | 大小 |",
        "| --- | --- |",
        *[
            f"| {name} | {size / 1024 / 1024:.1f} MiB |"
            for name, size in sizes.items()
        ],
        f"| **合计** | **{total / 1024 / 1024:.1f} MiB** |",
        "",
        f"安装后资源预算 ≤350 MiB：{'通过' if report['within_budget'] else '超预算'}",
        "",
    ]
    text = "\n".join(lines) + "\n"
    (BUILD / "size-report.md").write_text(text, encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(text)
    print(text)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建桌面资源")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-pgvector", action="store_true")
    parser.add_argument("--skip-sidecar", action="store_true")
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--clean-sidecar", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_fetch:
        fetch_postgres(args.force_fetch)
    if not args.skip_pgvector:
        build_pgvector()
    if not args.skip_sidecar:
        build_sidecar(args.clean_sidecar)

    sizes = stage_resources()
    write_report(sizes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
