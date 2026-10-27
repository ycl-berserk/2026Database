from __future__ import annotations

import pytest

from app.errors import NotFoundError
from app.services import conversation_service, search_service


def test_list_scope_follows_membership(conn, base):
    alice = conversation_service.list_conversations(conn, base["users"]["alice"])
    bob = conversation_service.list_conversations(conn, base["users"]["bob"])

    assert alice["total"] == 3
    assert bob["total"] == 2
    assert {row["title"] for row in bob["rows"]} == {"事务与隔离级别", "进程调度"}
    assert alice["total_pages"] == 1


def test_keyword_matches_message_and_builds_snippet(conn, base):
    result = conversation_service.list_conversations(conn, base["users"]["alice"], q="幻读")
    assert result["total"] == 1
    row = result["rows"][0]
    assert row["title"] == "事务与隔离级别"
    assert row["hit_seq"] == 3
    assert "幻读" in row["snippet"]
    assert row["message_count"] == 4


def test_filters_by_course_tag_and_date(conn, base):
    alice = base["users"]["alice"]
    conn.execute(
        "UPDATE app.conversations SET started_at = '2026-03-02T09:00:00+08:00' WHERE id = %s",
        (base["conversations"]["os-1"],),
    )
    conn.execute(
        "UPDATE app.conversations SET started_at = '2026-04-03T09:00:00+08:00' WHERE id = %s",
        (base["conversations"]["misc-1"],),
    )
    by_course = conversation_service.list_conversations(
        conn, alice, course_id=str(base["courses"]["OS"])
    )
    assert [row["title"] for row in by_course["rows"]] == ["进程调度"]

    uncategorized = conversation_service.list_conversations(conn, alice, course_id="none")
    assert [row["title"] for row in uncategorized["rows"]] == ["未归类的问题"]

    by_tag = conversation_service.list_conversations(conn, alice, tag_id=base["tag"])
    assert [row["title"] for row in by_tag["rows"]] == ["事务与隔离级别"]

    by_date = conversation_service.list_conversations(conn, alice, date_to="2026-02-02")
    assert by_date["total"] == 1
    assert by_date["rows"][0]["title"] == "事务与隔离级别"

    since_march = conversation_service.list_conversations(conn, alice, date_from="2026-03-01")
    assert since_march["total"] == 2


def test_pagination(conn, base):
    source_id = conn.execute(
        "SELECT source_id FROM app.conversations WHERE id = %s",
        (base["conversations"]["db-1"],),
    ).fetchone()["source_id"]
    for index in range(8):
        conn.execute(
            "INSERT INTO app.conversations (source_id, external_id, title) "
            "VALUES (%s, %s, %s)",
            (source_id, f"page-{index}", f"补充会话 {index}"),
        )

    first = conversation_service.list_conversations(conn, base["users"]["alice"], page=1)
    second = conversation_service.list_conversations(conn, base["users"]["alice"], page=2)
    assert first["total"] == 11
    assert first["total_pages"] == 2
    assert len(first["rows"]) == 10
    assert len(second["rows"]) == 1
    assert second["rows"][0]["title"] == "事务与隔离级别"


def test_filter_options_respect_access(conn, base):
    alice = conversation_service.filter_options(conn, base["users"]["alice"])
    assert {course["code"] for course in alice["courses"]} == {"DB", "OS"}
    assert alice["tags"][0]["name"] == "事务"
    assert alice["tags"][0]["usage_count"] == 1

    bob = conversation_service.filter_options(conn, base["users"]["bob"])
    assert {course["code"] for course in bob["courses"]} == {"DB", "OS"}
    assert [tag["name"] for tag in bob["tags"]] == ["事务"]


def test_conversation_detail_and_sender_filter(conn, base):
    alice = {"id": base["users"]["alice"], "system_role": "student"}
    detail = conversation_service.conversation_detail(
        conn, base["conversations"]["db-1"], alice
    )
    assert detail["counts"]["total"] == 4
    assert detail["counts"]["user_count"] == 2
    assert detail["counts"]["assistant_count"] == 2
    assert detail["can_edit"] is True
    assert {course["code"] for course in detail["manage_courses"]} == {"DB", "OS"}
    assert detail["selected_tag_ids"] == {base["tag"]}
    assert detail["messages"][1]["annotation_count"] == 1
    assert detail["messages"][1]["annotations"][0]["content"] == "隔离级别要能对比记忆。"

    assistant_only = conversation_service.conversation_detail(
        conn, base["conversations"]["db-1"], alice, sender="assistant"
    )
    assert len(assistant_only["messages"]) == 2
    assert all(row["sender"] == "assistant" for row in assistant_only["messages"])


def test_viewer_detail_is_read_only(conn, base):
    bob = {"id": base["users"]["bob"], "system_role": "student"}
    detail = conversation_service.conversation_detail(
        conn, base["conversations"]["db-1"], bob
    )
    assert detail["can_edit"] is False
    assert detail["manage_courses"] == []
    assert detail["my_tags"] == []


def test_unowned_conversation_is_hidden(conn, base):
    bob = {"id": base["users"]["bob"], "system_role": "student"}
    with pytest.raises(NotFoundError):
        conversation_service.get_conversation(conn, base["conversations"]["misc-1"], bob)


def test_make_snippet_windows_around_keyword():
    text = "前缀" * 80 + "目标关键词" + "后缀" * 80
    snippet = conversation_service.make_snippet(text, "目标关键词", limit=60)
    assert "目标关键词" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) <= 70
    assert conversation_service.make_snippet(None, "x") == ""
    assert conversation_service.make_snippet("abc", None, 2) == "ab…"


def _student(user_id: int) -> dict:
    return {"id": user_id, "system_role": "student"}


def test_global_search_matches_titles_messages_annotations_and_tags(conn, base):
    alice = _student(base["users"]["alice"])

    data = search_service.global_search(conn, alice, "隔离")
    assert [row["id"] for row in data["conversations"]["rows"]] == [
        base["conversations"]["db-1"]
    ]
    assert data["conversations"]["rows"][0]["hit_seq"] == 1
    assert len(data["annotations"]) == 1
    assert data["annotations"][0]["conversation_id"] == base["conversations"]["db-1"]

    tags = search_service.global_search(conn, alice, "事务")["tags"]
    assert [row["id"] for row in tags] == [base["tag"]]
    assert tags[0]["usage_count"] == 1

    courses = search_service.global_search(conn, alice, "数据库")["courses"]
    assert [row["id"] for row in courses] == [base["courses"]["DB"]]


def test_global_search_respects_course_scope(conn, base):
    bob = _student(base["users"]["bob"])

    accessible = search_service.global_search(conn, bob, "进程")
    assert accessible["conversations"]["total"] == 1
    assert accessible["conversations"]["rows"][0]["id"] == base["conversations"]["os-1"]

    hidden = search_service.global_search(conn, bob, "索引")
    assert hidden["conversations"]["total"] == 0
    assert hidden["annotations"] == []

    own_only = search_service.global_search(conn, bob, "隔离")
    assert own_only["conversations"]["total"] == 1
    assert own_only["annotations"] == []


def test_global_search_empty_query_returns_empty_groups(conn, base):
    data = search_service.global_search(conn, _student(base["users"]["alice"]), "   ")
    assert data["q"] == ""
    assert data["conversations"]["total"] == 0
    assert data["annotations"] == []
    assert data["courses"] == []
    assert data["tags"] == []
