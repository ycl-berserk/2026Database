from __future__ import annotations

import pytest

from app.errors import ValidationError
from app.services import admin_service, course_service


def _course_events(conn, course_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT actor_user_id, table_name, action, row_pk, changed_columns
          FROM app.audit_log
         WHERE (row_pk ->> 'id' = %s
                OR row_pk ->> 'course_id' = %s)
           AND table_name IN ('courses', 'course_members')
         ORDER BY id
        """,
        (str(course_id), str(course_id)),
    ).fetchall()


def test_course_writes_record_actor_pk_and_columns(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    bob_id = base["users"]["bob"]

    course_id = course_service.create_course(
        conn,
        {"code": "DB2", "name": "数据库进阶", "semester": "2026 秋"},
        alice["id"],
    )
    events = _course_events(conn, course_id)
    assert [(row["table_name"], row["action"]) for row in events] == [
        ("courses", "insert"),
        ("course_members", "insert"),
    ]
    assert all(row["actor_user_id"] == alice["id"] for row in events)
    course_event, member_event = events
    assert course_event["row_pk"] == {"id": course_id}
    assert {"code", "name", "semester"} <= set(course_event["changed_columns"])
    assert member_event["row_pk"] == {
        "course_id": course_id,
        "user_id": alice["id"],
    }

    course_service.update_course(
        conn,
        course_id,
        {"code": "DB2", "name": "数据库系统进阶", "semester": "2026 秋"},
        alice,
    )
    latest = _course_events(conn, course_id)[-1]
    assert latest["action"] == "update"
    assert latest["changed_columns"] == ["name"]
    assert latest["actor_user_id"] == alice["id"]

    course_service.add_member(conn, course_id, "bob", "viewer", alice)
    course_service.set_member_role(conn, course_id, bob_id, "editor", alice)
    member_update = _course_events(conn, course_id)[-1]
    assert member_update["table_name"] == "course_members"
    assert member_update["action"] == "update"
    assert member_update["changed_columns"] == ["role"]
    assert member_update["row_pk"]["user_id"] == bob_id

    course_service.remove_member(conn, course_id, bob_id, alice)
    assert _course_events(conn, course_id)[-1]["action"] == "delete"

    course_service.delete_course(conn, course_id, alice)
    deleted = [
        row for row in conn.execute(
            "SELECT action, row_pk FROM app.audit_log "
            "WHERE table_name = 'courses' AND action = 'delete'"
        ).fetchall()
        if row["row_pk"]["id"] == course_id
    ]
    assert len(deleted) == 1


def test_admin_toggle_is_audited_without_values(conn, base):
    admin_id = base["users"]["admin"]
    bob_id = base["users"]["bob"]

    admin_service.set_active(conn, bob_id, False, acting_user_id=admin_id)

    row = conn.execute(
        """
        SELECT actor_user_id, changed_columns, row_pk
          FROM app.audit_log
         WHERE table_name = 'users'
           AND action = 'update'
           AND row_pk ->> 'id' = %s
         ORDER BY id DESC
         LIMIT 1
        """,
        (str(bob_id),),
    ).fetchone()
    assert row["actor_user_id"] == admin_id
    assert row["row_pk"] == {"id": bob_id}
    assert row["changed_columns"] == ["is_active"]


def test_messages_and_annotations_are_not_audited(conn, base):
    conn.execute(
        "UPDATE app.annotations SET content = '不应进入审计的批注' WHERE id = %s",
        (base["annotation"],),
    )
    conn.execute(
        "UPDATE app.messages SET content = '不应进入审计的正文' WHERE id = %s",
        (base["messages"]["db-1"],),
    )

    rows = conn.execute(
        "SELECT table_name, changed_columns FROM app.audit_log"
    ).fetchall()
    assert {row["table_name"] for row in rows} <= {
        "courses",
        "course_members",
        "users",
    }
    audited_columns = {
        column for row in rows for column in row["changed_columns"]
    }
    assert "content" not in audited_columns
    assert "password_hash" not in audited_columns


def test_app_role_cannot_modify_audit_log(conn):
    row = conn.execute(
        """
        SELECT has_table_privilege('ls_app', 'app.audit_log', 'SELECT') AS can_read,
               has_table_privilege('ls_app', 'app.audit_log', 'INSERT') AS can_insert,
               has_table_privilege('ls_app', 'app.audit_log', 'UPDATE') AS can_update,
               has_table_privilege('ls_app', 'app.audit_log', 'DELETE') AS can_delete,
               has_function_privilege(
                   'ls_app', 'app.prune_audit_log(integer)', 'EXECUTE'
               ) AS can_prune
        """
    ).fetchone()
    assert row == {
        "can_read": True,
        "can_insert": False,
        "can_update": False,
        "can_delete": False,
        "can_prune": True,
    }


def test_prune_removes_only_expired_rows(conn, base):
    alice_id = base["users"]["alice"]
    for offset in ("120 days", "1 day"):
        conn.execute(
            """
            INSERT INTO app.audit_log
                (occurred_at, actor_user_id, table_name, action, row_pk, changed_columns)
            VALUES (now() - %s::interval, %s, 'users', 'update',
                    jsonb_build_object('id', %s), ARRAY['is_active'])
            """,
            (offset, alice_id, alice_id),
        )

    removed = admin_service.prune_audit(conn, 90)
    assert removed == 1
    remaining = conn.execute(
        "SELECT occurred_at FROM app.audit_log "
        "WHERE changed_columns = ARRAY['is_active']"
    ).fetchall()
    assert len(remaining) == 1
    assert remaining[0]["occurred_at"] > conn.execute(
        "SELECT now() - interval '90 days' AS cutoff"
    ).fetchone()["cutoff"]

    with pytest.raises(ValidationError):
        admin_service.prune_audit(conn, 7)
    with pytest.raises(ValidationError):
        admin_service.prune_audit(conn, 30)


def test_audit_filters_and_summary(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    course_service.create_course(
        conn,
        {"code": "NET2", "name": "网络进阶", "semester": "2026 秋"},
        alice["id"],
    )
    admin_service.set_active(
        conn, base["users"]["bob"], False, acting_user_id=base["users"]["admin"]
    )

    course_rows = admin_service.audit_events(conn, table="courses")
    assert course_rows and all(row["table_name"] == "courses" for row in course_rows)

    delete_rows = admin_service.audit_events(conn, action="delete")
    assert delete_rows == []

    summary = admin_service.audit_summary(conn)
    assert summary["event_count"] >= len(course_rows)
    assert summary["recent_count"] == summary["event_count"]
    assert summary["oldest_at"] is not None
