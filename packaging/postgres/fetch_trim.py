"""Fetch, verify, extract and trim the portable PostgreSQL runtime.

The archive is only downloaded once; its SHA-256 is pinned in manifest.json.
The raw tree is kept so pgvector can be built against the matching headers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "packaging" / ".cache"
BUILD = ROOT / "packaging" / "build"
MANIFEST = Path(__file__).with_name("manifest.json")

KEEP_EXES = {
    "postgres.exe",
    "initdb.exe",
    "pg_ctl.exe",
    "pg_isready.exe",
    "psql.exe",
    "pg_dump.exe",
    "pg_restore.exe",
}
DROP_TOP_LEVEL = {"include", "doc", "pgAdmin 4", "StackBuilder", "symbols"}
DROP_SHARE = {"locale", "man"}
# pgAdmin, StackBuilder and the docs add hundreds of megabytes (and Windows
# path-length failures); only the server, client tools and headers are needed.
KEEP_TOP_LEVEL = {"bin", "lib", "share", "include"}
# Everything else under lib/ is kept: the client tools depend on OpenSSL, ICU,
# libpq, zlib and friends, and their absence only shows up at runtime.
KEEP_EXTENSIONS = {"pg_trgm", "vector", "pg_stat_statements", "plpgsql"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def download(manifest: dict[str, object], archive: Path, force: bool) -> Path:
    expected = str(manifest.get("sha256") or "")
    if not expected:
        raise SystemExit("manifest.json 缺少 sha256，拒绝下载未固定版本的归档。")
    if archive.exists() and not force:
        actual = sha256(archive)
        if actual != expected:
            archive.unlink()
            raise SystemExit(f"缓存归档校验失败：{actual}")
        return archive
    archive.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] {manifest['url']}")
    started = time.perf_counter()
    with urllib.request.urlopen(str(manifest["url"])) as response, archive.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 / total
                print(f"\r[fetch] {pct:5.1f}%  {human(done)} / {human(total)}", end="")
    print(f"\n[fetch] done in {time.perf_counter() - started:.1f}s")
    actual = sha256(archive)
    if actual != expected:
        archive.unlink()
        raise SystemExit(f"SHA-256 不匹配：期望 {expected}，实际 {actual}")
    return archive


def extract(archive: Path, raw_root: Path, archive_root: str, force: bool) -> Path:
    target = raw_root / archive_root
    marker = target / "bin" / "postgres.exe"
    if marker.exists() and not force:
        print(f"[extract] reuse {target}")
        return target
    if raw_root.exists():
        shutil.rmtree(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    print(f"[extract] {archive.name} -> {raw_root}")
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            parts = Path(info.filename).parts
            if not parts or parts[0] != archive_root:
                continue
            if len(parts) > 1 and parts[1] not in KEEP_TOP_LEVEL:
                continue
            bundle.extract(info, raw_root)
    if not marker.exists():
        raise SystemExit(f"归档布局不符合预期，未找到 {marker}")
    return target


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prune_bin(bin_dir: Path) -> list[str]:
    """Drop DLLs that no kept executable imports (pgAdmin's wxWidgets, etc.)."""
    try:
        import pefile
    except ImportError as exc:
        raise SystemExit(
            "缺少 pefile：运行 pip install -r packaging/requirements.txt 后重试。"
        ) from exc

    files = {
        item.name.lower(): item
        for item in bin_dir.iterdir()
        if item.is_file() and item.suffix.lower() in {".exe", ".dll"}
    }
    roots = [name for name in KEEP_EXES if name in files]
    keep: set[str] = set()
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in keep or name not in files:
            continue
        keep.add(name)
        try:
            pe = pefile.PE(str(files[name]), fast_load=True)
            directories = [pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
            try:
                directories.append(
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]
                )
            except KeyError:
                pass
            pe.parse_data_directories(directories=directories)
        except pefile.PEFormatError:
            continue
        for attribute in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(pe, attribute, []) or []:
                dependency = entry.dll.decode("ascii", "ignore").lower()
                if dependency in files:
                    queue.append(dependency)

    removed = sorted(set(files) - keep)
    for name in removed:
        files[name].unlink()
    return removed


def trim(raw: Path, out: Path, force: bool) -> None:
    if out.exists():
        if not force:
            print(f"[trim] reuse {out}")
            return
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    bin_dir = raw / "bin"
    for item in bin_dir.iterdir():
        if item.is_file() and (
            item.suffix.lower() == ".dll" or item.name.lower() in KEEP_EXES
        ):
            copy_file(item, out / "bin" / item.name)

    lib_dir = raw / "lib"
    for item in lib_dir.iterdir():
        if item.is_file() and item.suffix.lower() != ".lib":
            copy_file(item, out / "lib" / item.name)
        elif item.is_dir():
            # Extension shared libraries live in lib/postgresql on Windows.
            expected = item.name == "postgresql"
            for library in item.rglob("*"):
                if not library.is_file():
                    continue
                stem = library.stem.lower()
                if expected and any(stem == name or stem.startswith(name + "-") for name in KEEP_EXTENSIONS):
                    copy_file(library, out / "lib" / library.relative_to(lib_dir))

    share = raw / "share"
    for item in share.iterdir():
        if item.name in DROP_SHARE:
            continue
        if item.is_dir():
            shutil.copytree(item, out / "share" / item.name)
        else:
            copy_file(item, out / "share" / item.name)

    for dropped in DROP_TOP_LEVEL:
        if (raw / dropped).exists():
            print(f"[trim] dropped {dropped}")
    removed = prune_bin(out / "bin")
    if removed:
        print(f"[trim] removed {len(removed)} unused bin files")
    print(f"[trim] {human(tree_size(out))} -> {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("all", "raw", "trim"), default="all")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--force", action="store_true", help="重新解压并裁剪")
    parser.add_argument("--json", metavar="PATH")
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = str(manifest["version"])
    archive = CACHE / Path(str(manifest["url"])).name
    raw_root = CACHE / f"raw-{version}"
    out = BUILD / "pgsql"

    if args.stage in {"all", "raw"}:
        download(manifest, archive, args.force_download)
        raw = extract(archive, raw_root, str(manifest["archive_root"]), args.force)
    else:
        raw = raw_root / str(manifest["archive_root"])

    report = {
        "version": version,
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size if archive.exists() else 0,
        "raw": str(raw),
        "raw_bytes": tree_size(raw) if raw.exists() else 0,
    }
    if args.stage in {"all", "trim"}:
        trim(raw, out, args.force)
        report.update({"output": str(out), "output_bytes": tree_size(out)})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
