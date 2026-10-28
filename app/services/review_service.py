from __future__ import annotations

import uuid
from datetime import date, datetime

from ..config import settings
from ..errors import NotFoundError, ValidationError
from . import fsrs
from .conversation_service import make_snippet

RATING_LABELS = {"again": "重来", "hard": "困难", "good": "良好", "easy": "容易"}
ALGORITHMS = ("fsrs", "simple")


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


def _algorithm(value: str | None) -> str:
    name = (value or settings.review_algorithm or "fsrs").strip().lower()
    return name if name in ALGORITHMS else "fsrs"


def _rating(value: str | None) -> str:
    try:
        return fsrs.normalize_rating(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _fsrs_state(row: dict) -> fsrs.MemoryState:
    return fsrs.MemoryState(
        stability=float(row["fsrs_stability"] or 1.0),
        difficulty=float(row["fsrs_difficulty"] or 5.0),
        lapses=int(row["fsrs_lapses"] or 0),
    )


def _elapsed_days(last_reviewed_at) -> int:
    if isinstance(last_reviewed_at, str):
        try:
            last_reviewed_at = datetime.fromisoformat(last_reviewed_at)
        except ValueError:
            return 1
    if not isinstance(last_reviewed_at, datetime):
        return 0
    # 同一天多次复习按 1 天计，避免稳定性因 elapsed=0 而完全不增长。
    return max(1, (date.today() - last_reviewed_at.date()).days)


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
    rating: str | None = None,
    algorithm: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO app.review_logs
            (user_id, review_item_id, action, event_id,
             interval_days, due_date, review_count, rating, algorithm)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, event_id) DO NOTHING
        """,
        (
            user_id, review_id, action, event_id,
            interval_days, due_date, review_count, rating, algorithm,
        ),
    )


def review_once(
    conn,
    review_id: int,
    user_id: int,
    event_id: str | None = None,
    rating: str | None = None,
    algorithm: str | None = None,
) -> dict:
    """记录一次复习；同一 event_id 重放时返回当前状态且不重复累加。"""
    event = _parse_event_id(event_id)
    grade = _rating(rating)
    mode = _algorithm(algorithm)
    with conn.transaction():
        current = conn.execute(
            """
            SELECT id, status, interval_days, due_date, review_count,
                   last_reviewed_at, fsrs_stability, fsrs_difficulty, fsrs_lapses
              FROM app.review_items
             WHERE id = %s AND user_id = %s
             FOR UPDATE
            """,
            (review_id, user_id),
        ).fetchone()
        if current is None:
            raise NotFoundError("复习项不存在")
        if _existing_event(conn, user_id, event) is not None:
            return {**current, "duplicate": True, "rating": grade, "algorithm": mode}

        if mode == "fsrs":
            state = _fsrs_state(current) if current["review_count"] > 0 else None
            next_state, interval = fsrs.schedule(
                state,
                grade,
                _elapsed_days(current["last_reviewed_at"]),
                settings.review_retention,
            )
            values = {
                "stability": next_state.stability,
                "difficulty": next_state.difficulty,
                "lapses": next_state.lapses,
            }
        else:
            interval = min(int(current["interval_days"]) * 2, 60)
            values = {
                "stability": float(interval),
                "difficulty": float(current["fsrs_difficulty"] or 5.0),
                "lapses": int(current["fsrs_lapses"] or 0),
            }

        row = conn.execute(
            """
            UPDATE app.review_items
               SET review_count = review_count + 1,
                   interval_days = %(interval)s,
                   due_date = current_date + %(interval)s,
                   status = CASE WHEN status = 'pending' THEN 'reviewing' ELSE status END,
                   last_reviewed_at = now(),
                   fsrs_stability = COALESCE(%(stability)s, fsrs_stability),
                   fsrs_difficulty = COALESCE(%(difficulty)s, fsrs_difficulty),
                   fsrs_lapses = COALESCE(%(lapses)s, fsrs_lapses)
             WHERE id = %(id)s AND user_id = %(user_id)s
             RETURNING status, interval_days, due_date, review_count,
                       fsrs_stability, fsrs_difficulty, fsrs_lapses
            """,
            {
                "interval": interval,
                "id": review_id,
                "user_id": user_id,
                **values,
            },
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
            rating=grade,
            algorithm=mode,
        )
    return {**row, "duplicate": False, "rating": grade, "algorithm": mode}


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
                   due_date = current_date, last_reviewed_at = NULL,
                   fsrs_stability = 1.0, fsrs_difficulty = 5.0, fsrs_lapses = 0
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
