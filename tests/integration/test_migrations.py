from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from app.migrations import BASELINE_VERSION, apply_pending, pending_migrations


def test_registry_exists_and_records_baseline(db_url):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as conn:
        exists = conn.execute(
            "SELECT to_regclass('meta.schema_migrations') IS NOT NULL AS ok"
        ).fetchone()["ok"]
        assert exists is True
        versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM meta.schema_migrations")
        }
    assert BASELINE_VERSION in versions


def test_no_pending_migrations(db_url):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as conn:
        assert pending_migrations(conn) == []


def test_apply_pending_is_idempotent(db_url):
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as conn:
        assert apply_pending(conn) == []
        assert apply_pending(conn) == []
