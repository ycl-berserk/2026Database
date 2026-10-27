from __future__ import annotations

from typing import Any


def student_dashboard(conn, user_id: int) -> dict[str, Any]:
    accessible = """
        SELECT conv.id, conv.title, conv.course_id, conv.source_id, conv.started_at
          FROM app.conversations conv
          JOIN app.sources s ON s.id = conv.source_id
         WHERE s.owner_id = %s
            OR (conv.course_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM app.course_members cm
                 WHERE cm.course_id = conv.course_id AND cm.user_id = %s
            ))
    """
    stats = conn.execute(
        f"""
        WITH accessible_conv AS ({accessible})
        SELECT
            (SELECT count(*) FROM app.course_members WHERE user_id = %s) AS course_count,
            (SELECT count(*) FROM accessible_conv) AS conversation_count,
            (SELECT count(*) FROM app.messages m
               JOIN accessible_conv ac ON ac.id = m.conversation_id) AS message_count,
            (SELECT count(*) FROM app.review_items
              WHERE user_id = %s AND status <> 'mastered') AS pending_review_count
        """,
        (user_id, user_id, user_id, user_id),
    ).fetchone()

    recent = conn.execute(
        f"""
        WITH accessible_conv AS ({accessible})
        SELECT ac.id,
               ac.title,
               co.name AS course_name,
               s.platform,
               ac.started_at,
               count(m.id) AS message_count,
               max(m.created_at) AS last_message_at
          FROM accessible_conv ac
          LEFT JOIN app.courses co ON co.id = ac.course_id
          JOIN app.sources s ON s.id = ac.source_id
          LEFT JOIN app.messages m ON m.conversation_id = ac.id
         GROUP BY ac.id, ac.title, co.name, s.platform, ac.started_at
         ORDER BY last_message_at DESC NULLS LAST, ac.id DESC
         LIMIT 6
        """,
        (user_id, user_id),
    ).fetchall()

    reviews = conn.execute(
        """
        SELECT ri.id,
               ri.due_date,
               ri.status,
               a.kind,
               a.content,
               conv.id AS conversation_id,
               conv.title AS conversation_title
          FROM app.review_items ri
          JOIN app.annotations a ON a.id = ri.annotation_id
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations conv ON conv.id = m.conversation_id
         WHERE ri.user_id = %s
           AND ri.status <> 'mastered'
         ORDER BY (ri.due_date <= current_date) DESC, ri.due_date ASC, ri.id DESC
         LIMIT 6
        """,
        (user_id,),
    ).fetchall()

    activity = conn.execute(
        f"""
        WITH months AS (
            SELECT generate_series(
                date_trunc('month', current_date) - interval '7 months',
                date_trunc('month', current_date),
                interval '1 month'
            ) AS month_start
        ),
        accessible_conv AS ({accessible}),
        monthly AS (
            SELECT date_trunc('month', m.created_at) AS month_start,
                   count(*) AS message_count
              FROM app.messages m
              JOIN accessible_conv ac ON ac.id = m.conversation_id
             WHERE m.created_at >= date_trunc('month', current_date) - interval '7 months'
             GROUP BY 1
        )
        SELECT to_char(mo.month_start, 'YYYY-MM') AS month,
               coalesce(monthly.message_count, 0) AS message_count
          FROM months mo
          LEFT JOIN monthly ON monthly.month_start = mo.month_start
         ORDER BY mo.month_start
        """,
        (user_id, user_id),
    ).fetchall()

    activity_max = max((row["message_count"] for row in activity), default=0)
    return {
        "stats": stats,
        "recent": recent,
        "reviews": reviews,
        "activity": activity,
        "activity_max": activity_max,
        "is_admin": False,
    }


def admin_dashboard(conn) -> dict[str, Any]:
    stats = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM app.users) AS user_count,
            (SELECT count(*) FROM app.users WHERE is_active) AS active_user_count,
            (SELECT count(*) FROM app.courses) AS course_count,
            (SELECT count(*) FROM app.conversations) AS conversation_count,
            (SELECT count(*) FROM app.messages) AS message_count,
            (SELECT count(*) FROM app.annotations) AS annotation_count,
            (SELECT count(*) FROM app.review_items WHERE status <> 'mastered') AS pending_review_count
        """
    ).fetchone()
    users = conn.execute(
        """
        SELECT id, username, display_name, system_role, is_active, created_at
          FROM app.users
         ORDER BY created_at DESC, id DESC
         LIMIT 8
        """
    ).fetchall()
    return {"stats": stats, "users": users, "is_admin": True}
