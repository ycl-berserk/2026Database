from __future__ import annotations

import pytest

from app.migrations import discover_migrations


def test_discover_returns_empty_for_missing_directory(tmp_path):
    assert discover_migrations(tmp_path / "missing") == []


def test_discover_sorts_by_file_name(tmp_path):
    for name in ("002_second.sql", "001_first.sql"):
        (tmp_path / name).write_text("SELECT 1", encoding="utf-8")
    assert [path.stem for path in discover_migrations(tmp_path)] == [
        "001_first",
        "002_second",
    ]


def test_discover_rejects_invalid_name(tmp_path):
    (tmp_path / "add_users.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(ValueError, match="迁移文件名不合规"):
        discover_migrations(tmp_path)


def test_discover_rejects_uppercase_name(tmp_path):
    (tmp_path / "001_AddUsers.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(ValueError, match="迁移文件名不合规"):
        discover_migrations(tmp_path)
