from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.errors import NotFoundError, PermissionDenied, ValidationError
from app.services import annotation_service, review_service


def _student(user_id: int) -> dict:
    return {"id": user_id, "system_role": "student"}


def test_create_annotation_permissions(conn, base):
    alice = _student(base["users"]["alice"])
    bob = _student(base["users"]["bob"])

    created = annotation_service.create_annotation(
        conn, base["messages"]["db-1"], alice, "note", "隔离级别要能对比记忆。"
    )
    assert created["kind"] == "note"

    with pytest.raises(ValidationError):
        annotation_service.create_annotation(conn, base["messages"]["db-1"], alice, "bad", "x")
    with pytest.raises(ValidationError):
        annotation_service.create_annotation(conn, base["messages"]["db-1"], alice, "note", "  ")

    with pytest.raises(PermissionDenied):
        annotation_service.create_annotation(
            conn, base["messages"]["db-1"], bob, "note", "viewer 不能批注"
        )

    allowed = annotation_service.create_annotation(
        conn, base["messages"]["os-1"], bob, "question", "时间片轮转的优缺点？"
    )
    assert allowed["id"]


def test_update_and_delete_only_own_annotation(conn, base):
    alice = _student(base["users"]["alice"])
    bob = _student(base["users"]["bob"])

    annotation_service.update_annotation(
        conn, base["annotation"], alice, "summary", "四种隔离级别的对比要点。", "resolved"
    )
    row = conn.execute(
        "SELECT kind, content, status FROM app.annotations WHERE id = %s",
        (base["annotation"],),
    ).fetchone()
    assert row["kind"] == "summary"
    assert row["status"] == "resolved"

    with pytest.raises(PermissionDenied):
        annotation_service.update_annotation(
            conn, base["annotation"], bob, "note", "越权修改", "open"
        )
    with pytest.raises(PermissionDenied):
        annotation_service.delete_annotation(conn, base["annotation"], bob)

    annotation_service.delete_annotation(conn, base["annotation"], alice)
    assert conn.execute(
        "SELECT count(*) AS n FROM app.annotations WHERE id = %s", (base["annotation"],)
    ).fetchone()["n"] == 0


def test_add_to_review_is_idempotent_and_permissioned(conn, base):
    alice = _student(base["users"]["alice"])
    bob = _student(base["users"]["bob"])

    assert annotation_service.add_to_review(conn, base["annotation"], alice) is False

    created = annotation_service.create_annotation(
        conn, base["messages"]["db-3"], alice, "question", "快照隔离如何避免幻读？"
    )
    assert annotation_service.add_to_review(conn, created["id"], alice) is True
    assert annotation_service.add_to_review(conn, created["id"], alice) is False
    assert review_service.summary(conn, base["users"]["alice"])["total"] == 2

    with pytest.raises(PermissionDenied):
        annotation_service.add_to_review(conn, created["id"], bob)


def test_list_annotations_filters_and_pagination(conn, base):
    alice = _student(base["users"]["alice"])
    annotation_service.create_annotation(
        conn, base["messages"]["db-3"], alice, "question", "第二个问题"
    )

    all_rows = annotation_service.list_annotations(conn, base["users"]["alice"])
    assert all_rows["total"] == 2
    assert all_rows["total_pages"] == 1

    questions = annotation_service.list_annotations(
        conn, base["users"]["alice"], kind="question"
    )
    assert questions["total"] == 1
    assert questions["rows"][0]["kind"] == "question"
    assert questions["rows"][0]["snippet"]

    resolved = annotation_service.list_annotations(
        conn, base["users"]["alice"], status="resolved"
    )
    assert resolved["total"] == 0


def test_review_schedule_doubles_and_resets(conn, base):
    alice_id = base["users"]["alice"]
    review_id = conn.execute(
        "SELECT id FROM app.review_items WHERE user_id = %s", (alice_id,)
    ).fetchone()["id"]

    first = review_service.review_once(conn, review_id, alice_id)
    assert first["interval_days"] == 2
    assert first["review_count"] == 1
    assert first["due_date"] == date.today() + timedelta(days=2)
    assert review_service.summary(conn, alice_id)["reviewing"] == 1

    second = review_service.review_once(conn, review_id, alice_id)
    assert second["interval_days"] == 4
    assert second["due_date"] == date.today() + timedelta(days=4)

    review_service.mark_mastered(conn, review_id, alice_id)
    summary = review_service.summary(conn, alice_id)
    assert summary["mastered"] == 1
    assert summary["pending"] == 0
    assert review_service.list_reviews(conn, alice_id, "active") == []
    assert len(review_service.list_reviews(conn, alice_id, "mastered")) == 1

    review_service.reset_review(conn, review_id, alice_id)
    reset = conn.execute(
        "SELECT status, interval_days, review_count, due_date "
        "FROM app.review_items WHERE id = %s",
        (review_id,),
    ).fetchone()
    assert reset["status"] == "pending"
    assert reset["interval_days"] == 1
    assert reset["review_count"] == 0
    assert reset["due_date"] == date.today()

    review_service.delete_review(conn, review_id, alice_id)
    assert review_service.summary(conn, alice_id)["total"] == 0


def test_review_event_is_idempotent_and_logged(conn, base):
    alice_id = base["users"]["alice"]
    review_id = conn.execute(
        "SELECT id FROM app.review_items WHERE user_id = %s", (alice_id,)
    ).fetchone()["id"]
    event_id = str(uuid4())

    first = review_service.review_once(conn, review_id, alice_id, event_id=event_id)
    assert first["duplicate"] is False
    assert first["review_count"] == 1

    replay = review_service.review_once(conn, review_id, alice_id, event_id=event_id)
    assert replay["duplicate"] is True
    assert replay["review_count"] == 1
    assert replay["due_date"] == date.today() + timedelta(days=2)

    logs = conn.execute(
        "SELECT action, event_id, interval_days, review_count "
        "FROM app.review_logs WHERE user_id = %s ORDER BY id",
        (alice_id,),
    ).fetchall()
    assert len(logs) == 1
    assert logs[0]["action"] == "review"
    assert str(logs[0]["event_id"]) == event_id

    review_service.review_once(conn, review_id, alice_id, event_id=str(uuid4()))
    assert conn.execute(
        "SELECT count(*) AS n FROM app.review_logs WHERE user_id = %s", (alice_id,)
    ).fetchone()["n"] == 2

    with pytest.raises(ValidationError):
        review_service.review_once(conn, review_id, alice_id, event_id="not-a-uuid")


def test_review_logs_survive_item_delete_and_feed_heatmap(conn, base):
    alice_id = base["users"]["alice"]
    review_id = conn.execute(
        "SELECT id FROM app.review_items WHERE user_id = %s", (alice_id,)
    ).fetchone()["id"]
    review_service.review_once(conn, review_id, alice_id)
    review_service.mark_mastered(conn, review_id, alice_id)

    actions = [
        row["action"]
        for row in conn.execute(
            "SELECT action FROM app.review_logs WHERE user_id = %s ORDER BY id",
            (alice_id,),
        )
    ]
    assert actions == ["review", "mastered"]

    activity = review_service.activity(conn, alice_id, days=7)
    assert len(activity) == 7
    assert sum(row["count"] for row in activity) == 1
    assert activity[-1]["day"] == date.today()

    review_service.delete_review(conn, review_id, alice_id)
    survivor = conn.execute(
        "SELECT review_item_id FROM app.review_logs WHERE user_id = %s ORDER BY id LIMIT 1",
        (alice_id,),
    ).fetchone()
    assert survivor["review_item_id"] is None


def test_review_list_marks_overdue(conn, base):
    alice_id = base["users"]["alice"]
    conn.execute(
        "UPDATE app.review_items SET due_date = current_date - 3 WHERE user_id = %s",
        (alice_id,),
    )
    rows = review_service.list_reviews(conn, alice_id, "active")
    assert len(rows) == 1
    assert rows[0]["is_overdue"] is True
    assert rows[0]["is_due"] is True
    assert rows[0]["annotation_content"] == "隔离级别要能对比记忆。"
    assert rows[0]["snippet"]


def test_review_items_are_scoped_to_owner(conn, base):
    alice_id = base["users"]["alice"]
    bob_id = base["users"]["bob"]
    review_id = conn.execute(
        "SELECT id FROM app.review_items WHERE user_id = %s", (alice_id,)
    ).fetchone()["id"]

    with pytest.raises(NotFoundError):
        review_service.review_once(conn, review_id, bob_id)
    with pytest.raises(NotFoundError):
        review_service.mark_mastered(conn, review_id, bob_id)
    with pytest.raises(NotFoundError):
        review_service.delete_review(conn, review_id, bob_id)
