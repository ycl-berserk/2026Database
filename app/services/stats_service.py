from __future__ import annotations

from . import review_service

ACCESSIBLE = """
    SELECT v.id
      FROM app.conversations v
      JOIN app.sources s ON s.id = v.source_id
     WHERE s.owner_id = %(uid)s
        OR (v.course_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM app.course_members cm
             WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
        ))
"""


def load_stats(conn, user_id: int) -> dict:
    params = {"uid": user_id}

    overview = conn.execute(
        f"""
        WITH ac AS ({ACCESSIBLE})
        SELECT
            (SELECT count(*) FROM app.course_members WHERE user_id = %(uid)s) AS course_count,
            (SELECT count(*) FROM ac) AS conversation_count,
            (SELECT count(*) FROM app.messages m JOIN ac ON ac.id = m.conversation_id) AS message_count,
            (SELECT count(*) FROM app.annotations a
               JOIN app.messages m ON m.id = a.message_id
               JOIN ac ON ac.id = m.conversation_id) AS annotation_count,
            (SELECT count(*) FROM app.tags WHERE owner_id = %(uid)s) AS tag_count,
            (SELECT count(*) FROM app.review_items
              WHERE user_id = %(uid)s AND status <> 'mastered') AS pending_review_count,
            (SELECT count(*) FROM app.review_items
              WHERE user_id = %(uid)s AND status <> 'mastered'
                AND due_date <= current_date) AS due_review_count
        """,
        params,
    ).fetchone()

    courses = conn.execute(
        """
        SELECT c.id, c.code, c.name, cm.role,
               count(DISTINCT v.id) AS conversation_count,
               count(DISTINCT m.id) AS message_count,
               count(DISTINCT m.id) FILTER (WHERE m.sender = 'user') AS user_messages,
               count(DISTINCT m.id) FILTER (WHERE m.sender = 'assistant') AS assistant_messages,
               count(DISTINCT a.id) AS annotation_count,
               count(DISTINCT ri.id) AS review_count,
               RANK() OVER (ORDER BY count(DISTINCT m.id) DESC, c.code) AS msg_rank
          FROM app.courses c
          JOIN app.course_members cm ON cm.course_id = c.id AND cm.user_id = %(uid)s
          LEFT JOIN app.conversations v ON v.course_id = c.id
          LEFT JOIN app.messages m ON m.conversation_id = v.id
          LEFT JOIN app.annotations a ON a.message_id = m.id
          LEFT JOIN app.review_items ri ON ri.annotation_id = a.id AND ri.user_id = %(uid)s
         GROUP BY c.id, c.code, c.name, cm.role
         ORDER BY msg_rank, c.code
        """,
        params,
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
        ac AS ({ACCESSIBLE}),
        monthly AS (
            SELECT date_trunc('month', m.created_at) AS month_start,
                   count(*) AS message_count
              FROM app.messages m
              JOIN ac ON ac.id = m.conversation_id
             WHERE m.created_at >= date_trunc('month', current_date) - interval '7 months'
             GROUP BY 1
        )
        SELECT to_char(mo.month_start, 'YYYY-MM') AS month,
               coalesce(monthly.message_count, 0) AS message_count
          FROM months mo
          LEFT JOIN monthly ON monthly.month_start = mo.month_start
         ORDER BY mo.month_start
        """,
        params,
    ).fetchall()
    activity_max = max((row["message_count"] for row in activity), default=0)

    tags = conn.execute(
        f"""
        WITH ac AS ({ACCESSIBLE})
        SELECT t.id, t.name, t.color,
               count(*) AS link_count,
               count(DISTINCT ct.conversation_id) AS conversation_count
          FROM app.conversation_tags ct
          JOIN ac ON ac.id = ct.conversation_id
          JOIN app.tags t ON t.id = ct.tag_id
         GROUP BY t.id, t.name, t.color
         ORDER BY conversation_count DESC, link_count DESC, t.name
         LIMIT 12
        """,
        params,
    ).fetchall()

    annotation_kinds = conn.execute(
        f"""
        WITH ac AS ({ACCESSIBLE})
        SELECT a.kind,
               count(*) AS total,
               count(*) FILTER (WHERE a.status = 'resolved') AS resolved
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
          JOIN ac ON ac.id = m.conversation_id
         GROUP BY a.kind
         ORDER BY total DESC
        """,
        params,
    ).fetchall()

    review_statuses = conn.execute(
        """
        SELECT status, count(*) AS total,
               count(*) FILTER (WHERE due_date <= current_date) AS overdue
          FROM app.review_items
         WHERE user_id = %s
         GROUP BY status
         ORDER BY status
        """,
        (user_id,),
    ).fetchall()

    duplicates = conn.execute(
        f"""
        WITH ac AS ({ACCESSIBLE})
        SELECT md5(m.content) AS hash,
               min(m.content) AS sample,
               count(*) AS occurrences,
               count(DISTINCT m.conversation_id) AS conversation_count
          FROM app.messages m
          JOIN ac ON ac.id = m.conversation_id
         GROUP BY md5(m.content)
        HAVING count(*) > 1
         ORDER BY occurrences DESC, hash
         LIMIT 5
        """,
        params,
    ).fetchall()

    return {
        "overview": overview,
        "courses": courses,
        "activity": activity,
        "activity_max": activity_max,
        "tags": tags,
        "annotation_kinds": annotation_kinds,
        "review_statuses": review_statuses,
        "review_summary": review_service.summary(conn, user_id),
        "duplicates": duplicates,
    }


def stats_csv(data: dict) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "item", "metric", "value"])

    labels = {
        "course_count": "课程数",
        "conversation_count": "会话数",
        "message_count": "消息数",
        "annotation_count": "批注数",
        "tag_count": "标签数",
        "pending_review_count": "待复习",
        "due_review_count": "今日到期",
    }
    for key, value in data["overview"].items():
        writer.writerow(["overview", labels.get(key, key), "", value])

    for course in data["courses"]:
        name = f"{course['code']} {course['name']}"
        writer.writerow(["course", name, "会话", course["conversation_count"]])
        writer.writerow(["course", name, "消息", course["message_count"]])
        writer.writerow(["course", name, "提问", course["user_messages"]])
        writer.writerow(["course", name, "回答", course["assistant_messages"]])
        writer.writerow(["course", name, "批注", course["annotation_count"]])
        writer.writerow(["course", name, "复习项", course["review_count"]])

    for tag in data["tags"]:
        writer.writerow(["tag", tag["name"], "会话数", tag["conversation_count"]])

    for kind in data["annotation_kinds"]:
        writer.writerow(["annotation_kind", kind["kind"], "总数", kind["total"]])
        writer.writerow(["annotation_kind", kind["kind"], "已解决", kind["resolved"]])

    for activity in data["activity"]:
        writer.writerow(["month", activity["month"], "消息数", activity["message_count"]])

    for review in data["review_statuses"]:
        writer.writerow(["review", review["status"], "总数", review["total"]])
        writer.writerow(["review", review["status"], "逾期", review["overdue"]])

    return "\ufeff" + buffer.getvalue()
