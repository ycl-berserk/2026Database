from __future__ import annotations

import psycopg
import pytest


def test_schema_has_tables_and_views(conn):
    tables = conn.execute(
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'app'"
    ).fetchone()["n"]
    views = conn.execute(
        "SELECT count(*) AS n FROM information_schema.views WHERE table_schema = 't2s'"
    ).fetchone()["n"]
    assert tables == 14
    assert views == 13


def test_unique_username(conn, base):
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO app.users (username, password_hash, display_name) "
            "VALUES ('alice', %s, '重复用户')",
            ("x" * 40,),
        )


def test_single_owner_per_course(conn, base):
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO app.course_members (course_id, user_id, role) "
            "VALUES (%s, %s, 'owner')",
            (base["courses"]["DB"], base["users"]["bob"]),
        )


def test_message_seq_unique(conn, base):
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO app.messages (conversation_id, seq_no, sender, content) "
            "VALUES (%s, 1, 'user', '重复序号')",
            (base["conversations"]["db-1"],),
        )


def test_message_sender_check(conn, base):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO app.messages (conversation_id, seq_no, sender, content) "
            "VALUES (%s, 99, 'robot', '非法角色')",
            (base["conversations"]["db-1"],),
        )


def test_conversation_time_order_check(conn, base):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE app.conversations SET started_at = now(), ended_at = now() - interval '1 day' "
            "WHERE id = %s",
            (base["conversations"]["db-1"],),
        )


def test_delete_course_sets_conversation_null(conn, base):
    conn.execute("DELETE FROM app.courses WHERE id = %s", (base["courses"]["DB"],))
    row = conn.execute(
        "SELECT course_id FROM app.conversations WHERE id = %s",
        (base["conversations"]["db-1"],),
    ).fetchone()
    assert row["course_id"] is None


def test_delete_conversation_cascades_messages(conn, base):
    conn.execute(
        "DELETE FROM app.conversations WHERE id = %s",
        (base["conversations"]["db-1"],),
    )
    remaining = conn.execute(
        "SELECT count(*) AS n FROM app.messages WHERE conversation_id = %s",
        (base["conversations"]["db-1"],),
    ).fetchone()["n"]
    assert remaining == 0


def test_delete_annotation_cascades_review(conn, base):
    conn.execute("DELETE FROM app.annotations WHERE id = %s", (base["annotation"],))
    remaining = conn.execute(
        "SELECT count(*) AS n FROM app.review_items WHERE annotation_id = %s",
        (base["annotation"],),
    ).fetchone()["n"]
    assert remaining == 0


def test_tag_access_trigger_blocks_outsider(conn, base):
    tag = conn.execute(
        "INSERT INTO app.tags (owner_id, name) VALUES (%s, 'bob-tag') RETURNING id",
        (base["users"]["bob"],),
    ).fetchone()
    with pytest.raises(psycopg.Error):
        conn.execute(
            "INSERT INTO app.conversation_tags (conversation_id, tag_id, created_by) "
            "VALUES (%s, %s, %s)",
            (base["conversations"]["misc-1"], tag["id"], base["users"]["bob"]),
        )


def test_annotation_access_trigger_blocks_viewer(conn, base):
    with pytest.raises(psycopg.Error):
        conn.execute(
            "INSERT INTO app.annotations (message_id, user_id, kind, content) "
            "VALUES (%s, %s, 'note', '越权批注')",
            (base["messages"]["db-2"], base["users"]["bob"]),
        )
