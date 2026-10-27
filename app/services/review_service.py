from __future__ import annotations

from datetime import date

from ..errors import NotFoundError
from .conversation_service import make_snippet


def summary(conn, user_id: int) -> dict:
    return conn.execute(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status = 'pending') AS pending,
               count(*) FILTER (WHERE status = 'reviewing') AS reviewing,
               count(*) FILTER (WHERE status = 'mastered') AS mastered,
               count(*) FILTER (WHERE status <> 'mastered' AND due_date <= current_date) AS due
          FROM app.review_items
         WHERE user_id = %s
        """,
        (user_id,),
    ).fetchone()


def list_reviews(conn, user_id: int, scope: str = "active") -> list[dict]:
    if scope == "mastered":
        clause = "ri.status = 'mastered'"
    elif scope == "all":
        clause = "TRUE"
    else:
        scope = "active"
        clause = "ri.status IN ('pending', 'reviewing')"

    rows = conn.execute(
        f"""
        SELECT ri.id, ri.status, ri.due_date, ri.interval_days, ri.review_count,
               ri.last_reviewed_at,
               a.id AS annotation_id, a.kind, a.content AS annotation_content,
               a.status AS annotation_status,
               u.display_name AS author_name,
               m.id AS message_id, m.seq_no, m.sender, m.content AS message_content,
               v.id AS conversation_id, v.title AS conversation_title,
               c.code AS course_code
          FROM app.review_items ri
          JOIN app.annotations a ON a.id = ri.annotation_id
          JOIN app.users u ON u.id = a.user_id
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
          LEFT JOIN app.courses c ON c.id = v.course_id
         WHERE ri.user_id = %s AND {clause}
         ORDER BY (ri.due_date <= current_date) DESC,
                  ri.due_date ASC, ri.id ASC
        """,
        (user_id,),
    ).fetchall()
    today = date.today()
    for row in rows:
        row["is_overdue"] = row["due_date"] is not None and row["due_date"] < today
        row["is_due"] = row["due_date"] is not None and row["due_date"] <= today
        row["snippet"] = make_snippet(row["message_content"], None, 140)
    return rows


def _own(conn, review_id: int, user_id: int) -> dict:
    row = conn.execute(
        "SELECT id, status FROM app.review_items WHERE id = %s AND user_id = %s",
        (review_id, user_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("复习项不存在")
    return row


def review_once(conn, review_id: int, user_id: int) -> dict:
    _own(conn, review_id, user_id)
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE app.review_items
               SET review_count = review_count + 1,
                   interval_days = LEAST(interval_days * 2, 60),
                   due_date = current_date + LEAST(interval_days * 2, 60),
                   status = CASE WHEN status = 'pending' THEN 'reviewing' ELSE status END,
                   last_reviewed_at = now()
             WHERE id = %s AND user_id = %s
             RETURNING interval_days, due_date, review_count
            """,
            (review_id, user_id),
        ).fetchone()
    return row


def mark_mastered(conn, review_id: int, user_id: int) -> None:
    _own(conn, review_id, user_id)
    with conn.transaction():
        conn.execute(
            "UPDATE app.review_items SET status = 'mastered', last_reviewed_at = now() "
            "WHERE id = %s AND user_id = %s",
            (review_id, user_id),
        )


def reset_review(conn, review_id: int, user_id: int) -> None:
    _own(conn, review_id, user_id)
    with conn.transaction():
        conn.execute(
            """
            UPDATE app.review_items
               SET status = 'pending', interval_days = 1, review_count = 0,
                   due_date = current_date, last_reviewed_at = NULL
             WHERE id = %s AND user_id = %s
            """,
            (review_id, user_id),
        )


def delete_review(conn, review_id: int, user_id: int) -> None:
    _own(conn, review_id, user_id)
    with conn.transaction():
        conn.execute(
            "DELETE FROM app.review_items WHERE id = %s AND user_id = %s",
            (review_id, user_id),
        )
