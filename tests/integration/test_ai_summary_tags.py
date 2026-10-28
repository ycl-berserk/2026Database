from __future__ import annotations

import pytest

from app.errors import PermissionDenied
from app.services import conversation_service


def _ctx(user_id: int) -> dict:
    return {"id": user_id, "system_role": "student"}


def _summary_rows(conn, conversation_id: int, user_id: int):
    return conn.execute(
        """
        SELECT a.id, a.message_id, a.content
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
         WHERE m.conversation_id = %s AND a.user_id = %s AND a.kind = 'summary'
         ORDER BY a.id
        """,
        (conversation_id, user_id),
    ).fetchall()


def _linked_tags(conn, conversation_id: int):
    return conn.execute(
        """
        SELECT t.name, ct.created_by
          FROM app.conversation_tags ct
          JOIN app.tags t ON t.id = ct.tag_id
         WHERE ct.conversation_id = %s
         ORDER BY t.name
        """,
        (conversation_id,),
    ).fetchall()


def test_organize_creates_new_tags_and_summary(conn, base):
    alice = _ctx(base["users"]["alice"])
    conversation_id = base["conversations"]["db-1"]

    conversation_service.organize_conversation(
        conn,
        conversation_id,
        alice,
        course_id=str(base["courses"]["DB"]),
        tag_ids=[base["tag"]],
        new_tag_names=["隔离级别", "隔离级别", "   "],
        summary="  事务隔离级别摘要。  ",
    )

    tags = {row["name"]: row["created_by"] for row in _linked_tags(conn, conversation_id)}
    assert tags == {"事务": alice["id"], "隔离级别": alice["id"]}

    summaries = _summary_rows(conn, conversation_id, alice["id"])
    assert len(summaries) == 1
    assert summaries[0]["content"] == "事务隔离级别摘要。"
    assert summaries[0]["message_id"] == base["messages"]["db-4"]

    detail = conversation_service.conversation_detail(conn, conversation_id, alice)
    assert detail["summary_text"] == "事务隔离级别摘要。"


def test_organize_updates_and_clears_summary(conn, base):
    alice = _ctx(base["users"]["alice"])
    conversation_id = base["conversations"]["db-1"]
    common = dict(course_id=str(base["courses"]["DB"]), tag_ids=[], new_tag_names=[])

    conversation_service.organize_conversation(
        conn, conversation_id, alice, summary="第一版摘要", **common
    )
    conversation_service.organize_conversation(
        conn, conversation_id, alice, summary="第二版摘要", **common
    )
    summaries = _summary_rows(conn, conversation_id, alice["id"])
    assert len(summaries) == 1
    assert summaries[0]["content"] == "第二版摘要"

    # 清空勾选后，本人之前打的标签应被移除。
    assert _linked_tags(conn, conversation_id) == []

    conversation_service.organize_conversation(
        conn, conversation_id, alice, summary="", **common
    )
    assert _summary_rows(conn, conversation_id, alice["id"]) == []
    assert conversation_service.conversation_detail(
        conn, conversation_id, alice
    )["summary_text"] == ""


def test_viewer_cannot_organize_or_write_summary(conn, base):
    bob = _ctx(base["users"]["bob"])
    conversation_id = base["conversations"]["db-1"]
    before_tags = conversation_service.own_tags(conn, bob["id"])

    with pytest.raises(PermissionDenied):
        conversation_service.organize_conversation(
            conn,
            conversation_id,
            bob,
            course_id=str(base["courses"]["DB"]),
            tag_ids=[],
            new_tag_names=["越权标签"],
            summary="越权摘要",
        )

    assert conversation_service.own_tags(conn, bob["id"]) == before_tags
    assert _summary_rows(conn, conversation_id, bob["id"]) == []
    assert _linked_tags(conn, conversation_id) == [
        {"name": "事务", "created_by": base["users"]["alice"]}
    ]


def test_editor_can_organize_shared_course_conversation(conn, base):
    bob = _ctx(base["users"]["bob"])
    conversation_id = base["conversations"]["os-1"]

    conversation_service.organize_conversation(
        conn,
        conversation_id,
        bob,
        course_id=str(base["courses"]["OS"]),
        tag_ids=[],
        new_tag_names=["调度"],
        summary="编辑者视角摘要",
    )

    assert {row["name"]: row["created_by"] for row in _linked_tags(conn, conversation_id)} == {
        "调度": bob["id"]
    }
    summaries = _summary_rows(conn, conversation_id, bob["id"])
    assert len(summaries) == 1
    assert summaries[0]["content"] == "编辑者视角摘要"
    # 会话只有一条提问时，摘要回退挂在该条消息上。
    assert summaries[0]["message_id"] == base["messages"]["os-1"]
