from __future__ import annotations

from datetime import date, timedelta

from ..errors import NotFoundError, PermissionDenied, ValidationError
from ..permissions import get_course_role

PAGE_SIZE = 10
SENDERS = ("user", "assistant", "system")


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def make_snippet(text: str | None, keyword: str | None, limit: int = 170) -> str:
    if not text:
        return ""
    flat = " ".join(str(text).split())
    if not keyword:
        return flat[:limit] + ("…" if len(flat) > limit else "")
    lowered = flat.lower()
    needle = keyword.lower()
    pos = lowered.find(needle)
    if pos < 0:
        return flat[:limit] + ("…" if len(flat) > limit else "")
    start = max(0, pos - 45)
    end = min(len(flat), pos + len(keyword) + limit - 45)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return prefix + flat[start:end] + suffix


def filter_options(conn, user_id: int) -> dict:
    courses = conn.execute(
        """
        SELECT c.id, c.code, c.name
          FROM app.courses c
          JOIN app.course_members cm ON cm.course_id = c.id
         WHERE cm.user_id = %s
         ORDER BY c.code
        """,
        (user_id,),
    ).fetchall()
    tags = conn.execute(
        """
        SELECT t.id, t.name, t.color, count(DISTINCT ct.conversation_id) AS usage_count
          FROM app.tags t
          JOIN app.conversation_tags ct ON ct.tag_id = t.id
          JOIN app.conversations v ON v.id = ct.conversation_id
          JOIN app.sources s ON s.id = v.source_id
         WHERE s.owner_id = %s
            OR (v.course_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM app.course_members cm
                 WHERE cm.course_id = v.course_id AND cm.user_id = %s
            ))
         GROUP BY t.id, t.name, t.color
         ORDER BY usage_count DESC, t.name
         LIMIT 40
        """,
        (user_id, user_id),
    ).fetchall()
    return {"courses": courses, "tags": tags}


def list_conversations(
    conn,
    user_id: int,
    *,
    q: str | None = None,
    course_id: str | None = None,
    tag_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
) -> dict:
    q = (q or "").strip()[:100] or None
    pattern = _like(q) if q else None
    params: dict = {"uid": user_id, "pattern": pattern, "limit": PAGE_SIZE}

    clauses = [
        """(
            s.owner_id = %(uid)s
            OR (v.course_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM app.course_members cm
                 WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
            ))
        )"""
    ]

    if course_id == "none":
        clauses.append("v.course_id IS NULL")
    elif course_id and str(course_id).isdigit():
        clauses.append("v.course_id = %(course_id)s")
        params["course_id"] = int(course_id)

    if tag_id:
        clauses.append(
            "EXISTS (SELECT 1 FROM app.conversation_tags ct "
            "WHERE ct.conversation_id = v.id AND ct.tag_id = %(tag_id)s)"
        )
        params["tag_id"] = tag_id

    start = _parse_date(date_from)
    if start:
        clauses.append("coalesce(v.started_at, v.created_at) >= %(date_from)s")
        params["date_from"] = start
    end = _parse_date(date_to)
    if end:
        clauses.append("coalesce(v.started_at, v.created_at) < %(date_to)s")
        params["date_to"] = end + timedelta(days=1)

    if pattern:
        clauses.append(
            """(
                v.title ILIKE %(pattern)s ESCAPE '\\'
                OR EXISTS (
                    SELECT 1 FROM app.messages mq
                     WHERE mq.conversation_id = v.id
                       AND mq.content ILIKE %(pattern)s ESCAPE '\\'
                )
            )"""
        )

    where_sql = " AND ".join(clauses)
    page = max(1, page)
    params["offset"] = (page - 1) * PAGE_SIZE

    rows = conn.execute(
        f"""
        SELECT v.id, v.title, v.course_id, v.started_at, v.is_archived,
               c.name AS course_name, c.code AS course_code,
               s.platform, s.account_label,
               (SELECT count(*) FROM app.messages m WHERE m.conversation_id = v.id) AS message_count,
               (SELECT max(m.created_at) FROM app.messages m WHERE m.conversation_id = v.id) AS last_message_at,
               (SELECT coalesce(array_agg(t.name ORDER BY t.name), '{{}}')
                  FROM app.conversation_tags ct
                  JOIN app.tags t ON t.id = ct.tag_id
                 WHERE ct.conversation_id = v.id) AS tag_names,
               hit.seq_no AS hit_seq,
               hit.sender AS hit_sender,
               hit.content AS hit_content,
               count(*) OVER() AS total
          FROM app.conversations v
          JOIN app.sources s ON s.id = v.source_id
          LEFT JOIN app.courses c ON c.id = v.course_id
          LEFT JOIN LATERAL (
              SELECT m.seq_no, m.sender, m.content
                FROM app.messages m
               WHERE m.conversation_id = v.id
                 AND (%(pattern)s::text IS NULL OR m.content ILIKE %(pattern)s ESCAPE '\\')
               ORDER BY m.seq_no
               LIMIT 1
          ) hit ON true
         WHERE {where_sql}
         ORDER BY coalesce(
                    (SELECT max(m.created_at) FROM app.messages m WHERE m.conversation_id = v.id),
                    v.started_at, v.created_at
                  ) DESC NULLS LAST,
                  v.id DESC
         LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()

    total = rows[0]["total"] if rows else 0
    for row in rows:
        row["snippet"] = make_snippet(row["hit_content"], q)
    return {
        "rows": rows,
        "q": q or "",
        "course_id": course_id or "",
        "tag_id": tag_id or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
        "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 0,
    }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def get_conversation(conn, conversation_id: int, user: dict) -> dict:
    row = conn.execute(
        """
        SELECT v.id, v.title, v.external_id, v.started_at, v.ended_at,
               v.course_id, v.is_archived, v.created_at,
               c.name AS course_name, c.code AS course_code,
               s.id AS source_id, s.owner_id, s.platform, s.account_label
          FROM app.conversations v
          JOIN app.sources s ON s.id = v.source_id
          LEFT JOIN app.courses c ON c.id = v.course_id
         WHERE v.id = %s
           AND (
                s.owner_id = %s
                OR (v.course_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id AND cm.user_id = %s
                ))
           )
        """,
        (conversation_id, user["id"], user["id"]),
    ).fetchone()
    if row is None:
        raise NotFoundError("会话不存在，或你没有访问权限")
    return row


def can_edit_conversation(conn, conversation: dict, user: dict) -> bool:
    if conversation["owner_id"] == user["id"]:
        return True
    if conversation["course_id"] is None:
        return False
    role = get_course_role(conn, conversation["course_id"], user["id"])
    return role in ("owner", "editor")


def editable_courses(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT c.id, c.code, c.name
          FROM app.courses c
          JOIN app.course_members cm ON cm.course_id = c.id
         WHERE cm.user_id = %s AND cm.role IN ('owner', 'editor')
         ORDER BY c.code
        """,
        (user_id,),
    ).fetchall()


def own_tags(conn, user_id: int) -> list[dict]:
    return conn.execute(
        "SELECT id, name, color FROM app.tags WHERE owner_id = %s ORDER BY name",
        (user_id,),
    ).fetchall()


def organize_conversation(
    conn,
    conversation_id: int,
    user: dict,
    *,
    course_id: str | None,
    tag_ids: list[int],
) -> None:
    conversation = get_conversation(conn, conversation_id, user)
    if not can_edit_conversation(conn, conversation, user):
        raise PermissionDenied("你没有编辑该会话的权限")

    target_course: int | None = None
    if course_id and course_id not in ("", "none"):
        try:
            target_course = int(course_id)
        except ValueError as exc:
            raise ValidationError("课程选择无效") from exc
        allowed = {row["id"] for row in editable_courses(conn, user["id"])}
        if target_course not in allowed:
            raise PermissionDenied("只能归类到自己可编辑的课程")

    owned = {row["id"] for row in own_tags(conn, user["id"])}
    selected = set(tag_ids) & owned

    with conn.transaction():
        conn.execute(
            "UPDATE app.conversations SET course_id = %s WHERE id = %s",
            (target_course, conversation_id),
        )
        conn.execute(
            "DELETE FROM app.conversation_tags "
            "WHERE conversation_id = %s AND created_by = %s",
            (conversation_id, user["id"]),
        )
        for tag_id in sorted(selected):
            conn.execute(
                """
                INSERT INTO app.conversation_tags (conversation_id, tag_id, created_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (conversation_id, tag_id) DO NOTHING
                """,
                (conversation_id, tag_id, user["id"]),
            )


def conversation_detail(
    conn,
    conversation_id: int,
    user: dict,
    *,
    sender: str | None = None,
    q: str | None = None,
) -> dict:
    conversation = get_conversation(conn, conversation_id, user)
    sender = sender if sender in SENDERS else None
    q = (q or "").strip()[:100] or None

    params = [conversation_id]
    sender_clause = ""
    if sender:
        sender_clause = " AND m.sender = %s"
        params.append(sender)

    messages = conn.execute(
        f"""
        SELECT m.id, m.seq_no, m.sender, m.content, m.created_at,
               count(a.id) FILTER (WHERE a.id IS NOT NULL) AS annotation_count
          FROM app.messages m
          LEFT JOIN app.annotations a ON a.message_id = m.id
         WHERE m.conversation_id = %s{sender_clause}
         GROUP BY m.id
         ORDER BY m.seq_no
        """,
        tuple(params),
    ).fetchall()

    message_ids = [row["id"] for row in messages]
    annotations: dict[int, list[dict]] = {}
    if message_ids:
        for row in conn.execute(
            """
            SELECT a.id, a.message_id, a.user_id, a.kind, a.content, a.status,
                   a.created_at, a.updated_at, u.display_name
              FROM app.annotations a
              JOIN app.users u ON u.id = a.user_id
             WHERE a.message_id = ANY(%s)
             ORDER BY a.created_at
            """,
            (message_ids,),
        ).fetchall():
            annotations.setdefault(row["message_id"], []).append(row)

    for row in messages:
        row["annotations"] = annotations.get(row["id"], [])

    tags = conn.execute(
        """
        SELECT t.id, t.name, t.kind, t.color, t.owner_id
          FROM app.conversation_tags ct
          JOIN app.tags t ON t.id = ct.tag_id
         WHERE ct.conversation_id = %s
         ORDER BY t.name
        """,
        (conversation_id,),
    ).fetchall()

    counts = conn.execute(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE sender = 'user') AS user_count,
               count(*) FILTER (WHERE sender = 'assistant') AS assistant_count
          FROM app.messages WHERE conversation_id = %s
        """,
        (conversation_id,),
    ).fetchone()

    can_edit = can_edit_conversation(conn, conversation, user)
    return {
        "conversation": conversation,
        "messages": messages,
        "tags": tags,
        "counts": counts,
        "sender": sender or "",
        "q": q or "",
        "can_edit": can_edit,
        "course_role": (
            get_course_role(conn, conversation["course_id"], user["id"])
            if conversation["course_id"]
            else None
        ),
        "manage_courses": editable_courses(conn, user["id"]) if can_edit else [],
        "my_tags": own_tags(conn, user["id"]) if can_edit else [],
        "selected_tag_ids": {
            tag["id"] for tag in tags if tag["owner_id"] == user["id"]
        },
    }
