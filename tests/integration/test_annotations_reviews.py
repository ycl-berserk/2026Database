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


def test_review_schedule_follows_fsrs_and_resets(conn, base):
    alice_id = base["users"]["alice"]
    review_id = conn.execute(
        "SELECT id FROM app.review_items WHERE user_id = %s", (alice_id,)
    ).fetchone()["id"]

    first = review_service.review_once(conn, review_id, alice_id)
    assert first["interval_days"] == 2
    assert first["review_count"] == 1
    assert first["due_date"] == date.today() + timedelta(days=2)
    assert first["algorithm"] == "fsrs"
    assert review_service.summary(conn, alice_id)["reviewing"] == 1

    second = review_service.review_once(conn, review_id, alice_id)
    assert second["interval_days"] == 7
    assert second["due_date"] == date.today() + timedelta(days=7)

    review_service.mark_mastered(conn, review_id, alice_id)
    summary = review_service.summary(conn, alice_id)
    assert summary["mastered"] == 1
    assert summary["pending"] == 0
    assert review_service.list_reviews(conn, alice_id, "active") == []
    assert len(review_service.list_reviews(conn, alice_id, "mastered")) == 1

    review_service.reset_review(conn, review_id, alice_id)
    reset = conn.execute(
        "SELECT status, interval_days, review_count, due_date, "
        "       fsrs_stability, fsrs_difficulty, fsrs_lapses "
        "FROM app.review_items WHERE id = %s",
        (review_id,),
    ).fetchone()
    assert reset["status"] == "pending"
    assert reset["interval_days"] == 1
    assert reset["review_count"] == 0
    assert reset["due_date"] == date.today()
    assert reset["fsrs_stability"] == 1.0
    assert reset["fsrs_difficulty"] == 5.0
    assert reset["fsrs_lapses"] == 0

    review_service.delete_review(conn, review_id, alice_id)
    assert review_service.summary(conn, alice_id)["total"] == 0


def test_fsrs_ratings_and_simple_fallback(conn, base):
    alice = _student(base["users"]["alice"])
    expected = {"again": 1, "hard": 1, "good": 2, "easy": 8}
    review_ids = {}
    for message_key, rating in zip(
        ("db-1", "db-3", "db-4", "os-1"), expected, strict=True
    ):
        annotation = annotation_service.create_annotation(
            conn, base["messages"][message_key], alice, "note", f"评分 {rating}"
        )
        assert annotation_service.add_to_review(conn, annotation["id"], alice) is True
        review_id = conn.execute(
            "SELECT id FROM app.review_items WHERE annotation_id = %s",
            (annotation["id"],),
        ).fetchone()["id"]
        row = review_service.review_once(
            conn, review_id, alice["id"], rating=rating
        )
        assert row["interval_days"] == expected[rating]
        assert row["rating"] == rating
        review_ids[rating] = review_id

    difficulties = {
        rating: conn.execute(
            "SELECT fsrs_difficulty FROM app.review_items WHERE id = %s",
            (review_ids[rating],),
        ).fetchone()["fsrs_difficulty"]
        for rating in expected
    }
    assert (
        difficulties["again"]
        > difficulties["hard"]
        > difficulties["good"]
        > difficulties["easy"]
    )

    logs = conn.execute(
        "SELECT rating, algorithm FROM app.review_logs "
        "WHERE user_id = %s ORDER BY id",
        (alice["id"],),
    ).fetchall()
    assert [row["rating"] for row in logs] == list(expected)
    assert all(row["algorithm"] == "fsrs" for row in logs)

    with pytest.raises(ValidationError):
        review_service.review_once(
            conn, review_ids["good"], alice["id"], rating="perfect"
        )

    simple_id = conn.execute(
        "SELECT id FROM app.review_items WHERE annotation_id = %s",
        (base["annotation"],),
    ).fetchone()["id"]
    first = review_service.review_once(
        conn, simple_id, alice["id"], algorithm="simple"
    )
    second = review_service.review_once(
        conn, simple_id, alice["id"], algorithm="simple"
    )
    assert (first["interval_days"], second["interval_days"]) == (2, 4)
    assert second["algorithm"] == "simple"


def test_annotation_event_id_is_idempotent(conn, base):
    alice = _student(base["users"]["alice"])
    event_id = str(uuid4())

    first = annotation_service.create_annotation(
        conn,
        base["messages"]["db-1"],
        alice,
        "note",
        "离线草稿第一次提交",
        event_id=event_id,
    )
    assert first["duplicate"] is False

    replay = annotation_service.create_annotation(
        conn,
        base["messages"]["db-1"],
        alice,
        "note",
        "重放时不应再插入",
        event_id=event_id,
    )
    assert replay["duplicate"] is True
    assert replay["id"] == first["id"]
    assert replay["content"] == first["content"]
    assert conn.execute(
        "SELECT count(*) AS n FROM app.annotations "
        "WHERE user_id = %s AND client_event_id = %s",
        (alice["id"], event_id),
    ).fetchone()["n"] == 1

    with pytest.raises(ValidationError):
        annotation_service.create_annotation(
            conn,
            base["messages"]["db-1"],
            alice,
            "note",
            "坏事件标识",
            event_id="not-a-uuid",
        )


def test_mastered_and_reset_events_are_idempotent(conn, base):
    alice_id = base["users"]["alice"]
    review_id = conn.execute(
        "SELECT id FROM app.review_items WHERE user_id = %s", (alice_id,)
    ).fetchone()["id"]

    mastered_event = str(uuid4())
    first = review_service.mark_mastered(
        conn, review_id, alice_id, event_id=mastered_event
    )
    assert first["duplicate"] is False
    assert first["status"] == "mastered"
    replay = review_service.mark_mastered(
        conn, review_id, alice_id, event_id=mastered_event
    )
    assert replay["duplicate"] is True

    reset_event = str(uuid4())
    reset = review_service.reset_review(conn, review_id, alice_id, event_id=reset_event)
    assert reset["duplicate"] is False
    replay = review_service.reset_review(conn, review_id, alice_id, event_id=reset_event)
    assert replay["duplicate"] is True

    logs = conn.execute(
        "SELECT action FROM app.review_logs WHERE user_id = %s ORDER BY id",
        (alice_id,),
    ).fetchall()
    assert [row["action"] for row in logs] == ["mastered", "reset"]


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
