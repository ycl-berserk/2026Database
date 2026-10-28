from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.backup_service import (
    assert_restore_target,
    build_pg_dump_command,
    build_pg_restore_command,
    connection_params,
    dump_filename,
    with_target_database,
)

URL = "postgresql://backup:pa%40ss@db.local:5433/learning_space"


def test_connection_params_decodes_credentials():
    assert connection_params(URL) == {
        "host": "db.local",
        "port": "5433",
        "user": "backup",
        "password": "pa@ss",
        "dbname": "learning_space",
    }
    with pytest.raises(ValueError):
        connection_params("mysql://host/db")
    with pytest.raises(ValueError):
        connection_params("postgresql://host")


def test_with_target_database_keeps_credentials():
    assert (
        with_target_database("postgresql://u:p@h:5433/postgres", "learning_space_test")
        == "postgresql://u:p@h:5433/learning_space_test"
    )


def test_dump_filename_is_utc_timestamped():
    moment = datetime(2026, 10, 28, 9, 5, 7, tzinfo=UTC)
    assert dump_filename(moment) == "learning-space-20261028-090507.dump"


def test_pg_dump_and_restore_commands_are_explicit():
    dump = build_pg_dump_command(URL, "data/backups/x.dump", "pg_dump")
    assert dump[0] == "pg_dump"
    assert "--format=custom" in dump
    assert dump[-2:] == ["--file", "data/backups/x.dump"]

    restore = build_pg_restore_command(URL, "x.dump", "pg_restore")
    assert restore[0] == "pg_restore"
    assert "--clean" in restore and "--if-exists" in restore
    assert restore[-1] == "x.dump"


def test_restore_only_targets_test_database_without_force():
    assert_restore_target("learning_space_test", force=False)
    with pytest.raises(ValueError):
        assert_restore_target("learning_space", force=False)
    assert_restore_target("learning_space", force=True)
