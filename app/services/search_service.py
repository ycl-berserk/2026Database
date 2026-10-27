from __future__ import annotations

from .conversation_service import _like, list_conversations, make_snippet


def global_search(conn, user: dict, q: str) -> dict:
    q = (q or "").strip()[:100]
    if not q:
        return {
            "q": "",
            "conversations": {"rows": [], "total": 0},
            "annotations": [],
            "courses": [],
            "tags": [],
        }
    pattern = _like(q)
    uid = user["id"]

    conversations = list_conversations(conn, uid, q=q, page=1)

    annotations = conn.execute(
        """
        SELECT a.id, a.kind, a.content, a.updated_at,
               m.id AS message_id, m.seq_no,
               v.id AS conversation_id, v.title AS conversation_title
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
         WHERE a.user_id = %(uid)s
           AND a.content ILIKE %(pattern)s ESCAPE '\\'
           AND (
                s.owner_id = %(uid)s
                OR (v.course_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
                ))
           )
         ORDER BY a.updated_at DESC, a.id DESC
         LIMIT 8
        """,
        {"uid": uid, "pattern": pattern},
    ).fetchall()
    for row in annotations:
        row["snippet"] = make_snippet(row["content"], q, 130)

    courses = conn.execute(
        """
        SELECT c.id, c.code, c.name, c.semester, c.instructor,
               (SELECT count(*) FROM app.conversations v WHERE v.course_id = c.id) AS conversation_count
          FROM app.courses c
         WHERE (
                c.created_by = %(uid)s
                OR EXISTS (SELECT 1 FROM app.course_members cm
                            WHERE cm.course_id = c.id AND cm.user_id = %(uid)s)
               )
           AND (c.code ILIKE %(pattern)s ESCAPE '\\'
                OR c.name ILIKE %(pattern)s ESCAPE '\\'
                OR coalesce(c.instructor, '') ILIKE %(pattern)s ESCAPE '\\')
         ORDER BY c.name
         LIMIT 6
        """,
        {"uid": uid, "pattern": pattern},
    ).fetchall()

    tags = conn.execute(
        """
        SELECT t.id, t.name, t.kind, t.color,
               (SELECT count(*) FROM app.conversation_tags ct WHERE ct.tag_id = t.id) AS usage_count
          FROM app.tags t
         WHERE t.owner_id = %(uid)s
           AND t.name ILIKE %(pattern)s ESCAPE '\\'
         ORDER BY t.name
         LIMIT 10
        """,
        {"uid": uid, "pattern": pattern},
    ).fetchall()

    return {
        "q": q,
        "conversations": conversations,
        "annotations": annotations,
        "courses": courses,
        "tags": tags,
    }
