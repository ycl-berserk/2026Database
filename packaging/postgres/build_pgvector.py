"""Build pgvector against the pinned EDB PostgreSQL tree with MSVC.

The PowerShell version depended on cmdlets that are not guaranteed to load in
every CI environment (Get-FileHash was missing on the runner), so the download,
hash check and extraction live in Python and only the MSVC build runs in a
cmd batch.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.8.0"
SHA256 = "6951d2b333f28b3c439ac5aa612bd3ec922529fc1c557a84338cc6b99e04437e"
URL = f"https://codeload.github.com/pgvector/pgvector/zip/refs/tags/v{VERSION}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(work: Path) -> Path:
    archive = work / f"pgvector-v{VERSION}.zip"
    if not archive.exists():
        print(f"[pgvector] downloading v{VERSION}", flush=True)
        archive.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, archive)
    actual = sha256(archive)
    if actual != SHA256:
        archive.unlink()
        raise SystemExit(f"pgvector archive SHA-256 mismatch: {actual}")
    return archive


def find_vcvars() -> Path:
    vswhere = (
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio"
        / "Installer"
        / "vswhere.exe"
    )
    if not vswhere.exists():
        raise SystemExit("vswhere.exe not found; install Visual Studio Build Tools with C++.")
    result = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    install = result.stdout.strip()
    if not install:
        raise SystemExit("No Visual Studio C++ toolchain found.")
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.exists():
        raise SystemExit(f"vcvars64.bat not found under {install}")
    return vcvars


def link_pgsql(work: Path, pg_root: Path) -> Path:
    link = work / "pgsql"
    if link.exists():
        os.rmdir(link)  # removes the junction, never the target
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(pg_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"cannot create junction {link}: {result.stderr or result.stdout}")
    return link


def build_env() -> dict[str, str]:
    # Git Bash / CI shells export SHELL=/bin/bash.exe, which makes nmake run
    # rules through bash where `mkdir` resolves to GNU coreutils (no parent
    # directories). Drop it and pin PATH to the system directories; vcvars64
    # appends the MSVC toolchain inside the batch.
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    env = os.environ.copy()
    env.pop("SHELL", None)
    env["PATH"] = os.pathsep.join(
        [
            str(Path(system_root) / "System32"),
            system_root,
            str(Path(system_root) / "System32" / "Wbem"),
            str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0"),
        ]
    )
    return env


def run_build(source: Path, link: Path, vcvars: Path, work: Path) -> None:
    batch = work / "build.bat"
    batch.write_text(
        "\r\n".join(
            [
                "@echo off",
                'set "SHELL="',
                f'call "{vcvars}" >nul',
                "if errorlevel 1 exit /b 1",
                f'cd /d "{source}"',
                f'nmake /nologo /F Makefile.win PGROOT="{link}" install',
            ]
        )
        + "\r\n",
        encoding="ascii",
    )
    subprocess.run(["cmd.exe", "/c", str(batch)], cwd=source, env=build_env(), check=True)


def install(source: Path, install_dir: Path) -> None:
    (install_dir / "lib").mkdir(parents=True, exist_ok=True)
    extension = install_dir / "share" / "extension"
    extension.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "vector.dll", install_dir / "lib")
    shutil.copy2(source / "vector.control", extension)
    for script in (source / "sql").glob("vector--*.sql"):
        shutil.copy2(script, extension)
    print(f"[pgvector] installed into {install_dir}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-root", required=True, type=Path, help="raw EDB tree")
    parser.add_argument("--install-dir", required=True, type=Path, help="trimmed PG tree")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", r"C:\Temp"))
        / "LearningSpaceBuild"
        / "pgvector",
    )
    args = parser.parse_args(argv)

    if os.name != "nt":
        raise SystemExit("build_pgvector.py only supports Windows; use the macOS shell script")

    pg_root = args.pg_root.resolve()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    archive = download(work)

    source = work / f"pgvector-{VERSION}"
    if source.exists():
        shutil.rmtree(source)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(work)

    link = link_pgsql(work, pg_root)

    # nmake's install rule creates this directory unconditionally; the raw tree
    # is reused across builds, so clear leftovers to keep the install repeatable.
    header_dir = pg_root / "include" / "server" / "extension" / "vector"
    if header_dir.exists():
        shutil.rmtree(header_dir)

    run_build(source, link, find_vcvars(), work)
    install(source, args.install_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
