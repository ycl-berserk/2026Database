from __future__ import annotations

from app.services import stats_service


def test_overview_counts_for_owner(conn, base):
    stats = stats_service.load_stats(conn, base["users"]["alice"])
    overview = stats["overview"]
    assert overview["course_count"] == 2
    assert overview["conversation_count"] == 3
    assert overview["message_count"] == 6
    assert overview["annotation_count"] == 1
    assert overview["tag_count"] == 1
    assert overview["pending_review_count"] == 1
    assert overview["due_review_count"] == 1


def test_course_stats_and_rank(conn, base):
    stats = stats_service.load_stats(conn, base["users"]["alice"])
    courses = {row["code"]: row for row in stats["courses"]}
    assert set(courses) == {"DB", "OS"}
    assert courses["DB"]["conversation_count"] == 1
    assert courses["DB"]["message_count"] == 4
    assert courses["DB"]["user_messages"] == 2
    assert courses["DB"]["assistant_messages"] == 2
    assert courses["DB"]["annotation_count"] == 1
    assert courses["DB"]["review_count"] == 1
    assert courses["DB"]["msg_rank"] == 1
    assert courses["OS"]["msg_rank"] == 2
    assert courses["DB"]["role"] == "owner"


def test_activity_shape_has_eight_months(conn, base):
    stats = stats_service.load_stats(conn, base["users"]["alice"])
    assert len(stats["activity"]) == 8
    months = [row["month"] for row in stats["activity"]]
    assert months == sorted(months)
    assert all(len(month) == 7 and month[4] == "-" for month in months)
    assert stats["activity_max"] == max(row["message_count"] for row in stats["activity"])


def test_tags_annotations_and_reviews(conn, base):
    stats = stats_service.load_stats(conn, base["users"]["alice"])
    assert [row["name"] for row in stats["tags"]] == ["事务"]
    assert stats["tags"][0]["conversation_count"] == 1
    assert stats["tags"][0]["link_count"] == 1

    assert stats["annotation_kinds"] == [
        {"kind": "note", "total": 1, "resolved": 0}
    ]
    assert stats["review_statuses"] == [{"status": "pending", "total": 1, "overdue": 1}]
    assert stats["review_summary"]["due"] == 1
    assert stats["duplicates"] == []


def test_duplicate_message_detection(conn, base):
    conn.execute(
        "INSERT INTO app.messages (conversation_id, seq_no, sender, content) "
        "VALUES (%s, 2, 'user', %s)",
        (base["conversations"]["misc-1"], "事务的隔离级别有哪些？"),
    )
    stats = stats_service.load_stats(conn, base["users"]["alice"])
    assert len(stats["duplicates"]) == 1
    duplicate = stats["duplicates"][0]
    assert duplicate["occurrences"] == 2
    assert duplicate["conversation_count"] == 2
    assert duplicate["sample"] == "事务的隔离级别有哪些？"


def test_member_sees_shared_conversations_only(conn, base):
    stats = stats_service.load_stats(conn, base["users"]["bob"])
    overview = stats["overview"]
    assert overview["course_count"] == 2
    assert overview["conversation_count"] == 2
    assert overview["message_count"] == 5
    assert overview["annotation_count"] == 1
    assert overview["tag_count"] == 0
    assert overview["pending_review_count"] == 0

    courses = {row["code"]: row for row in stats["courses"]}
    assert courses["DB"]["role"] == "viewer"
    assert courses["OS"]["role"] == "editor"


def test_stats_csv_export(conn, base):
    data = stats_service.load_stats(conn, base["users"]["alice"])
    text = stats_service.stats_csv(data)
    assert text.startswith("\ufeff")
    assert "section,item,metric,value" in text
    assert "课程数" in text
    assert "DB 数据库系统概论" in text
    assert "事务" in text
