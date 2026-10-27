from __future__ import annotations

import uuid
from datetime import date

from ..errors import NotFoundError, ValidationError
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


def _parse_event_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError("复习事件标识无效") from exc


def _existing_event(conn, user_id: int, event_id: str | None) -> dict | None:
    if not event_id:
        return None
    return conn.execute(
        "SELECT id FROM app.review_logs WHERE user_id = %s AND event_id = %s",
        (user_id, event_id),
    ).fetchone()


def _log_event(
    conn,
    *,
    user_id: int,
    review_id: int,
    action: str,
    event_id: str | None,
    interval_days: int,
    due_date,
    review_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO app.review_logs
            (user_id, review_item_id, action, event_id,
             interval_days, due_date, review_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, event_id) DO NOTHING
        """,
        (
            user_id, review_id, action, event_id,
            interval_days, due_date, review_count,
        ),
    )


def review_once(
    conn, review_id: int, user_id: int, event_id: str | None = None
) -> dict:
    """记录一次复习；同一 event_id 重放时返回当前状态且不重复累加。"""
    event = _parse_event_id(event_id)
    with conn.transaction():
        current = conn.execute(
            """
            SELECT id, status, interval_days, due_date, review_count
              FROM app.review_items
             WHERE id = %s AND user_id = %s
             FOR UPDATE
            """,
            (review_id, user_id),
        ).fetchone()
        if current is None:
            raise NotFoundError("复习项不存在")
        if _existing_event(conn, user_id, event) is not None:
            return {**current, "duplicate": True}

        row = conn.execute(
            """
            UPDATE app.review_items
               SET review_count = review_count + 1,
                   interval_days = LEAST(interval_days * 2, 60),
                   due_date = current_date + LEAST(interval_days * 2, 60),
                   status = CASE WHEN status = 'pending' THEN 'reviewing' ELSE status END,
                   last_reviewed_at = now()
             WHERE id = %s AND user_id = %s
             RETURNING status, interval_days, due_date, review_count
            """,
            (review_id, user_id),
        ).fetchone()
        _log_event(
            conn,
            user_id=user_id,
            review_id=review_id,
            action="review",
            event_id=event,
            interval_days=row["interval_days"],
            due_date=row["due_date"],
            review_count=row["review_count"],
        )
    return {**row, "duplicate": False}


def mark_mastered(
    conn, review_id: int, user_id: int, event_id: str | None = None
) -> dict:
    event = _parse_event_id(event_id)
    with conn.transaction():
        current = conn.execute(
            "SELECT id, status, interval_days, due_date, review_count "
            "FROM app.review_items WHERE id = %s AND user_id = %s FOR UPDATE",
            (review_id, user_id),
        ).fetchone()
        if current is None:
            raise NotFoundError("复习项不存在")
        if _existing_event(conn, user_id, event) is not None:
            return {**current, "duplicate": True}
        row = conn.execute(
            """
            UPDATE app.review_items
               SET status = 'mastered', last_reviewed_at = now()
             WHERE id = %s AND user_id = %s
             RETURNING status, interval_days, due_date, review_count
            """,
            (review_id, user_id),
        ).fetchone()
        _log_event(
            conn,
            user_id=user_id,
            review_id=review_id,
            action="mastered",
            event_id=event,
            interval_days=row["interval_days"],
            due_date=row["due_date"],
            review_count=row["review_count"],
        )
    return {**row, "duplicate": False}


def reset_review(
    conn, review_id: int, user_id: int, event_id: str | None = None
) -> dict:
    event = _parse_event_id(event_id)
    with conn.transaction():
        current = conn.execute(
            "SELECT id, status, interval_days, due_date, review_count "
            "FROM app.review_items WHERE id = %s AND user_id = %s FOR UPDATE",
            (review_id, user_id),
        ).fetchone()
        if current is None:
            raise NotFoundError("复习项不存在")
        if _existing_event(conn, user_id, event) is not None:
            return {**current, "duplicate": True}
        row = conn.execute(
            """
            UPDATE app.review_items
               SET status = 'pending', interval_days = 1, review_count = 0,
                   due_date = current_date, last_reviewed_at = NULL
             WHERE id = %s AND user_id = %s
             RETURNING status, interval_days, due_date, review_count
            """,
            (review_id, user_id),
        ).fetchone()
        _log_event(
            conn,
            user_id=user_id,
            review_id=review_id,
            action="reset",
            event_id=event,
            interval_days=row["interval_days"],
            due_date=row["due_date"],
            review_count=row["review_count"],
        )
    return {**row, "duplicate": False}


def activity(conn, user_id: int, days: int = 35) -> list[dict]:
    rows = conn.execute(
        """
        SELECT d::date AS day, count(l.id) AS count
          FROM generate_series(
                   current_date - (%(span)s::int - 1), current_date, interval '1 day'
               ) AS d
          LEFT JOIN app.review_logs l
                 ON l.user_id = %(uid)s
                AND l.action = 'review'
                AND l.created_at::date = d::date
         GROUP BY d
         ORDER BY d
        """,
        {"uid": user_id, "span": max(1, days)},
    ).fetchall()
    for row in rows:
        row["level"] = min(int(row["count"]), 4)
    return rows


def delete_review(conn, review_id: int, user_id: int) -> None:
    _own(conn, review_id, user_id)
    with conn.transaction():
        conn.execute(
            "DELETE FROM app.review_items WHERE id = %s AND user_id = %s",
            (review_id, user_id),
        )
