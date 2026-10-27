from __future__ import annotations

import pytest

from app.errors import NotFoundError, PermissionDenied
from app.services import conversation_service, import_service


def test_bulk_organize_only_moves_own_unclassified_conversations(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    bob = {"id": base["users"]["bob"], "system_role": "student"}

    moved = conversation_service.bulk_organize_inbox(
        conn, [base["conversations"]["misc-1"]], alice,
        course_id=base["courses"]["DB"],
    )
    assert moved == 1
    course_id = conn.execute(
        "SELECT course_id FROM app.conversations WHERE id = %s",
        (base["conversations"]["misc-1"],),
    ).fetchone()["course_id"]
    assert course_id == base["courses"]["DB"]

    # 已归类的会话不会被再次移动。
    assert conversation_service.bulk_organize_inbox(
        conn, [base["conversations"]["misc-1"]], alice,
        course_id=base["courses"]["OS"],
    ) == 0

    # bob 是 OS 的 editor，但收件箱只能整理自己名下的未归类会话。
    conn.execute(
        "UPDATE app.conversations SET course_id = NULL WHERE id = %s",
        (base["conversations"]["misc-1"],),
    )
    assert conversation_service.bulk_organize_inbox(
        conn, [base["conversations"]["misc-1"]], bob,
        course_id=base["courses"]["OS"],
    ) == 0


def test_bulk_organize_rejects_course_without_edit_permission(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    with conn.transaction():
        course = conn.execute(
            "INSERT INTO app.courses (code, name, semester, created_by) "
            "VALUES ('ALG', '算法', '2026 秋', %s) RETURNING id",
            (base["users"]["bob"],),
        ).fetchone()
        conn.execute(
            "INSERT INTO app.course_members (course_id, user_id, role) "
            "VALUES (%s, %s, 'owner')",
            (course["id"], base["users"]["bob"]),
        )
    with pytest.raises(PermissionDenied):
        conversation_service.bulk_organize_inbox(
            conn, [base["conversations"]["misc-1"]], alice,
            course_id=course["id"],
        )


def test_ingest_plain_text_creates_unclassified_conversation_and_deduplicates(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    text = "我：MVCC 是什么？\n助手：多版本并发控制，让读写互不阻塞。"

    first = import_service.ingest_plain_text(
        conn, user=alice, text=text, title="MVCC 速记", account_label="share"
    )
    assert first["conversations_new"] == 1
    assert first["messages_new"] == 2
    assert first["conversation_id"]

    conversation = conn.execute(
        "SELECT course_id, title FROM app.conversations WHERE id = %s",
        (first["conversation_id"],),
    ).fetchone()
    assert conversation["course_id"] is None
    assert conversation["title"] == "MVCC 速记"

    second = import_service.ingest_plain_text(
        conn, user=alice, text=text, title="MVCC 速记", account_label="share"
    )
    assert second["conversation_id"] == first["conversation_id"]
    assert second["conversations_new"] == 0
    assert second["messages_new"] == 0
    assert second["messages_skipped"] == 2


def test_ingest_plain_text_can_assign_editable_course(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    result = import_service.ingest_plain_text(
        conn,
        user=alice,
        text="用户：什么是事务？\n助手：一组原子操作。",
        course_id=base["courses"]["DB"],
        account_label="share",
    )
    course_id = conn.execute(
        "SELECT course_id FROM app.conversations WHERE id = %s",
        (result["conversation_id"],),
    ).fetchone()["course_id"]
    assert course_id == base["courses"]["DB"]


def test_get_batch_is_scoped_to_owner(conn, base):
    result, batch = import_service.stage_paste(
        conn, user_id=base["users"]["alice"], text="我：问题\n助手：回答"
    )
    assert result.ok and batch is not None
    loaded = import_service.get_batch(conn, batch["id"], base["users"]["alice"])
    assert loaded["id"] == batch["id"]
    with pytest.raises(NotFoundError):
        import_service.get_batch(conn, batch["id"], base["users"]["bob"])
