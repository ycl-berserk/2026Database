from __future__ import annotations

import re
from pathlib import Path

from .config import settings

BASELINE_VERSION = "000_baseline"
MIGRATIONS_DIR = settings.base_dir / "sql" / "migrations"
REGISTRY_RELATION = "meta.schema_migrations"
VERSION_PATTERN = re.compile(r"^\d{3}_[a-z0-9_]+$")


def _first_value(row):
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def ensure_registry(conn) -> None:
    conn.execute("CREATE SCHEMA IF NOT EXISTS meta")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta.schema_migrations (
            version     text        PRIMARY KEY,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def applied_versions(conn) -> set[str]:
    exists = _first_value(
        conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (REGISTRY_RELATION,)
        ).fetchone()
    )
    if not exists:
        return set()
    return {
        _first_value(row)
        for row in conn.execute("SELECT version FROM meta.schema_migrations")
    }


def discover_migrations(directory: Path | None = None) -> list[Path]:
    target = directory or MIGRATIONS_DIR
    if not target.exists():
        return []

    files = sorted(target.glob("*.sql"), key=lambda path: path.name)
    seen: dict[str, Path] = {}
    for path in files:
        version = path.stem
        if not VERSION_PATTERN.fullmatch(version):
            raise ValueError(
                f"迁移文件名不合规: {path.name}，应形如 001_add_api_tokens.sql"
            )
        key = version.lower()
        if key in seen:
            raise ValueError(f"迁移版本重复: {seen[key].name} 与 {path.name}")
        seen[key] = path
    return files


def pending_migrations(conn, directory: Path | None = None) -> list[str]:
    applied = applied_versions(conn)
    return [path.stem for path in discover_migrations(directory) if path.stem not in applied]


def record_baseline(conn) -> bool:
    cursor = conn.execute(
        "INSERT INTO meta.schema_migrations (version) VALUES (%s) "
        "ON CONFLICT (version) DO NOTHING",
        (BASELINE_VERSION,),
    )
    return cursor.rowcount == 1


def apply_pending(
    conn,
    *,
    directory: Path | None = None,
    mark_baseline: bool = True,
) -> list[str]:
    schema_ready = _first_value(
        conn.execute("SELECT to_regclass('app.users') IS NOT NULL").fetchone()
    )
    if not schema_ready:
        raise RuntimeError("数据库尚未初始化，请先运行 scripts/init_db.py --reset")

    ensure_registry(conn)
    if mark_baseline:
        record_baseline(conn)

    applied = applied_versions(conn)
    newly_applied: list[str] = []
    for path in discover_migrations(directory):
        version = path.stem
        if version in applied:
            continue
        script = path.read_text(encoding="utf-8")
        with conn.transaction():
            if script.strip():
                conn.execute(script)
            conn.execute(
                "INSERT INTO meta.schema_migrations (version) VALUES (%s)",
                (version,),
            )
        newly_applied.append(version)
        applied.add(version)
    return newly_applied
