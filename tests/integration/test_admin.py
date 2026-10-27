from __future__ import annotations

import uuid

import pytest

from app.errors import NotFoundError, ValidationError
from app.services import admin_service


def test_system_stats(conn, base):
    stats = admin_service.system_stats(conn)
    assert stats["user_count"] == 3
    assert stats["active_user_count"] == 3
    assert stats["course_count"] == 2
    assert stats["membership_count"] == 4
    assert stats["source_count"] == 1
    assert stats["conversation_count"] == 3
    assert stats["message_count"] == 6
    assert stats["annotation_count"] == 1
    assert stats["review_item_count"] == 1
    assert stats["import_batch_count"] == 0
    assert stats["t2s_log_count"] == 0


def test_list_users_with_keyword_and_counts(conn, base):
    rows = admin_service.list_users(conn)
    assert [row["username"] for row in rows] == ["alice", "bob", "admin"]
    alice = rows[0]
    assert alice["course_count"] == 2
    assert alice["source_count"] == 1
    assert alice["conversation_count"] == 3
    assert alice["annotation_count"] == 1

    assert [row["username"] for row in admin_service.list_users(conn, "ali")] == ["alice"]
    assert [row["username"] for row in admin_service.list_users(conn, "周")] == ["bob"]
    assert admin_service.list_users(conn, "nobody") == []


def test_set_active_rules(conn, base):
    admin_id = base["users"]["admin"]
    bob_id = base["users"]["bob"]

    with pytest.raises(ValidationError):
        admin_service.set_active(conn, admin_id, False, acting_user_id=admin_id)
    with pytest.raises(NotFoundError):
        admin_service.set_active(conn, 9999, False, acting_user_id=admin_id)

    row = admin_service.set_active(conn, bob_id, False, acting_user_id=admin_id)
    assert row["is_active"] is True
    assert conn.execute(
        "SELECT is_active FROM app.users WHERE id = %s", (bob_id,)
    ).fetchone()["is_active"] is False

    admin_service.set_active(conn, bob_id, True, acting_user_id=admin_id)
    assert conn.execute(
        "SELECT is_active FROM app.users WHERE id = %s", (bob_id,)
    ).fetchone()["is_active"] is True


def test_recent_activity_only_aggregates(conn, base):
    conn.execute(
        """
        INSERT INTO app.import_batches
            (user_id, source_id, file_name, file_sha256, file_size, preview_token, expires_at)
        VALUES (%s, (SELECT id FROM app.sources LIMIT 1), 'demo.json', %s, 32, %s,
                now() + interval '30 minutes')
        """,
        (base["users"]["alice"], "a" * 64, uuid.uuid4()),
    )
    rows = admin_service.recent_activity(conn)
    assert len(rows) == 1
    assert rows[0]["file_name"] == "demo.json"
    assert rows[0]["platform"] == "qoder"
    assert rows[0]["display_name"] == "林晓"
    assert rows[0]["status"] == "preview"
    assert admin_service.system_stats(conn)["import_batch_count"] == 1
