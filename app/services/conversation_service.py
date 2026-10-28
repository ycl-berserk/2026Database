from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta

from ..config import settings
from ..errors import NotFoundError, PermissionDenied, ValidationError
from ..permissions import get_course_role
from . import embedding_service, tag_service

PAGE_SIZE = 10
SENDERS = ("user", "assistant", "system")
CURSOR_VERSION = 1
SORT_EXPR = (
    "coalesce((SELECT max(m.created_at) FROM app.messages m"
    " WHERE m.conversation_id = v.id), v.started_at, v.created_at)"
)


def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
        return None
    return data


def _lexical_key(state: dict | None) -> tuple[str, int] | None:
    if not state or state.get("m") != "lexical":
        return None
    stamp, conversation_id = state.get("t"), state.get("i")
    if not isinstance(stamp, str) or not isinstance(conversation_id, int):
        return None
    try:
        datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return stamp, conversation_id


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


def rrf_fuse(rank_lists: list[list[int]], k: int | None = None) -> list[int]:
    k = settings.hybrid_rrf_k if k is None else k
    scores: dict[int, float] = {}
    for ids in rank_lists:
        for rank, conversation_id in enumerate(ids, start=1):
            scores[conversation_id] = scores.get(conversation_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda conversation_id: (-scores[conversation_id], conversation_id))


def _hybrid_candidates(conn, filters_sql: str, params: dict, q: str, vector: list[float]):
    lexical = conn.execute(
        f"""
        SELECT v.id
          FROM app.conversations v
          JOIN app.sources s ON s.id = v.source_id
          LEFT JOIN app.courses c ON c.id = v.course_id
         WHERE {filters_sql}
           AND (
                v.title ILIKE %(pattern)s ESCAPE '\\'
                OR EXISTS (
                    SELECT 1 FROM app.messages mq
                     WHERE mq.conversation_id = v.id
                       AND mq.content ILIKE %(pattern)s ESCAPE '\\'
                )
           )
         ORDER BY coalesce(
                      (SELECT max(m.created_at) FROM app.messages m
                        WHERE m.conversation_id = v.id),
                      v.started_at, v.created_at
                  ) DESC NULLS LAST,
                  v.id DESC
         LIMIT %(candidate_limit)s
        """,
        params,
    ).fetchall()
    vector_rows = conn.execute(
        f"""
        SELECT m.conversation_id AS id
          FROM app.messages m
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
          LEFT JOIN app.courses c ON c.id = v.course_id
         WHERE m.embedding IS NOT NULL
           AND {filters_sql}
         GROUP BY m.conversation_id
         ORDER BY min(m.embedding <=> %(vector)s::vector)
         LIMIT %(candidate_limit)s
        """,
        {**params, "vector": embedding_service.vector_literal(vector)},
    ).fetchall()
    return [row["id"] for row in lexical], [row["id"] for row in vector_rows]


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
    cursor: str | None = None,
    direction: str = "after",
    query_vector: list[float] | None = None,
) -> dict:
    q = (q or "").strip()[:100] or None
    pattern = _like(q) if q else None
    params: dict = {
        "uid": user_id,
        "pattern": pattern,
        "limit": PAGE_SIZE,
        "candidate_limit": settings.hybrid_candidate_limit,
    }

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

    filters_sql = " AND ".join(clauses)
    query_sql = filters_sql
    if pattern:
        query_sql += (
            """ AND (
                v.title ILIKE %(pattern)s ESCAPE '\\'
                OR EXISTS (
                    SELECT 1 FROM app.messages mq
                     WHERE mq.conversation_id = v.id
                       AND mq.content ILIKE %(pattern)s ESCAPE '\\'
                )
            )"""
        )

    if q and query_vector is None:
        query_vector = embedding_service.embed_query(q)
    hybrid = bool(q and query_vector and settings.hybrid_search_enabled)

    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE
    state = decode_cursor(cursor)
    direction = direction if direction in ("after", "before") else "after"
    key = None if hybrid else _lexical_key(state)
    retrieval = {"mode": "lexical", "rrf_k": settings.hybrid_rrf_k, "candidates": None}
    page_ids: list[int] | None = None
    total = 0
    next_cursor: str | None = None
    prev_cursor: str | None = None

    if hybrid:
        assert q is not None and query_vector is not None
        lexical_ids, vector_ids = _hybrid_candidates(
            conn, filters_sql, params, q, query_vector
        )
        fused = rrf_fuse([lexical_ids, vector_ids])
        total = len(fused)
        start = offset
        embedded = state.get("n") if state else None
        if (
            state
            and state.get("m") == "hybrid"
            and isinstance(state.get("p"), int)
        ):
            position = max(0, int(state["p"]))
            if isinstance(embedded, int) and embedded >= 0:
                total = embedded
            start = (
                max(0, position - PAGE_SIZE)
                if direction == "before"
                else min(total, position + PAGE_SIZE)
            )
        end = min(total, start + PAGE_SIZE)
        page_ids = fused[start:end]
        if start > 0:
            prev_cursor = encode_cursor(
                {"v": CURSOR_VERSION, "m": "hybrid", "p": start, "n": total}
            )
        if end < total:
            next_cursor = encode_cursor(
                {"v": CURSOR_VERSION, "m": "hybrid", "p": end, "n": total}
            )
        retrieval = {
            "mode": "hybrid",
            "rrf_k": settings.hybrid_rrf_k,
            "candidates": {
                "lexical": len(lexical_ids),
                "vector": len(vector_ids),
                "fused": total,
            },
        }
        rows = []
        if page_ids:
            params["page_ids"] = page_ids
            assert query_vector is not None
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
                       hit.content AS hit_content
                  FROM app.conversations v
                  JOIN app.sources s ON s.id = v.source_id
                  LEFT JOIN app.courses c ON c.id = v.course_id
                  LEFT JOIN LATERAL (
                      SELECT m.seq_no, m.sender, m.content
                        FROM app.messages m
                       WHERE m.conversation_id = v.id
                         AND (
                              (%(pattern)s::text IS NOT NULL
                               AND m.content ILIKE %(pattern)s ESCAPE '\\')
                              OR (%(embedding)s::text IS NOT NULL
                                  AND m.embedding IS NOT NULL)
                         )
                       ORDER BY
                         CASE WHEN %(pattern)s::text IS NOT NULL
                                   AND m.content ILIKE %(pattern)s ESCAPE '\\'
                              THEN 0 ELSE 1 END,
                         CASE WHEN %(embedding)s::text IS NOT NULL
                              THEN m.embedding <=> %(embedding)s::vector
                              ELSE m.seq_no END
                       LIMIT 1
                  ) hit ON true
                 WHERE {filters_sql} AND v.id = ANY(%(page_ids)s)
                 ORDER BY coalesce(
                              (SELECT max(m.created_at) FROM app.messages m
                                WHERE m.conversation_id = v.id),
                              v.started_at, v.created_at
                          ) DESC NULLS LAST,
                          v.id DESC
                """,
                {**params, "embedding": embedding_service.vector_literal(query_vector)},
            ).fetchall()
            order = {conversation_id: index for index, conversation_id in enumerate(page_ids)}
            rows.sort(key=lambda row: order[row["id"]])
    else:
        params["embedding"] = None
        params["limit"] = PAGE_SIZE + 1
        where_sql = query_sql
        order_sql = f"{SORT_EXPR} DESC, v.id DESC"
        tail_sql = ""
        if key:
            comparator = ">" if direction == "before" else "<"
            where_sql += (
                f" AND ({SORT_EXPR}, v.id) {comparator}"
                " (%(c_ts)s::timestamptz, %(c_id)s)"
            )
            params["c_ts"] = key[0]
            params["c_id"] = key[1]
            if direction == "before":
                order_sql = f"{SORT_EXPR} ASC, v.id ASC"
        else:
            params["offset"] = offset
            tail_sql = " OFFSET %(offset)s"
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
                   {SORT_EXPR} AS sort_at,
                   count(*) OVER() AS total
              FROM app.conversations v
              JOIN app.sources s ON s.id = v.source_id
              LEFT JOIN app.courses c ON c.id = v.course_id
              LEFT JOIN LATERAL (
                  SELECT m.seq_no, m.sender, m.content
                    FROM app.messages m
                   WHERE m.conversation_id = v.id
                     AND (%(pattern)s::text IS NULL
                          OR m.content ILIKE %(pattern)s ESCAPE '\\')
                   ORDER BY m.seq_no
                   LIMIT 1
              ) hit ON true
             WHERE {where_sql}
             ORDER BY {order_sql}
             LIMIT %(limit)s{tail_sql}
            """,
            params,
        ).fetchall()
        if direction == "before" and key:
            rows.reverse()
        extra = len(rows) > PAGE_SIZE
        if extra:
            rows = rows[:PAGE_SIZE]
        embedded = state.get("n") if state else None
        if key and isinstance(embedded, int) and embedded >= 0:
            total = embedded
        else:
            total = rows[0]["total"] if rows else 0
        if rows:
            first_key = (rows[0]["sort_at"].isoformat(), rows[0]["id"])
            last_key = (rows[-1]["sort_at"].isoformat(), rows[-1]["id"])

            def make_cursor(item: tuple[str, int]) -> str:
                return encode_cursor(
                    {
                        "v": CURSOR_VERSION,
                        "m": "lexical",
                        "t": item[0],
                        "i": item[1],
                        "n": total,
                    }
                )

            if direction == "before" and key:
                if extra:
                    prev_cursor = make_cursor(first_key)
                next_cursor = make_cursor(last_key)
            else:
                if extra:
                    next_cursor = make_cursor(last_key)
                if key:
                    prev_cursor = make_cursor(first_key)

    needle = (q or "").lower()
    for row in rows:
        content = row["hit_content"] or ""
        if needle and needle in content.lower():
            row["hit_mode"] = "lexical"
        elif hybrid and row["hit_content"]:
            row["hit_mode"] = "vector"
        else:
            row["hit_mode"] = "none"
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
        "next_cursor": next_cursor,
        "prev_cursor": prev_cursor,
        "retrieval": retrieval,
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


def _summary_annotation(conn, conversation_id: int, user_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT a.id, a.content
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
         WHERE m.conversation_id = %s
           AND a.user_id = %s
           AND a.kind = 'summary'
         ORDER BY a.updated_at DESC
         LIMIT 1
        """,
        (conversation_id, user_id),
    ).fetchone()


def _apply_summary(conn, conversation_id: int, user_id: int, content: str | None) -> None:
    if content is None:
        return
    text = content.strip()
    if len(text) > 4000:
        raise ValidationError("摘要不能超过 4000 字符")
    existing = _summary_annotation(conn, conversation_id, user_id)
    if not text:
        if existing is not None:
            conn.execute("DELETE FROM app.annotations WHERE id = %s", (existing["id"],))
        return
    if existing is not None:
        conn.execute(
            "UPDATE app.annotations SET content = %s WHERE id = %s",
            (text, existing["id"]),
        )
        return
    message = conn.execute(
        """
        SELECT id
          FROM app.messages
         WHERE conversation_id = %s
         ORDER BY CASE WHEN sender = 'assistant' THEN 0 ELSE 1 END, seq_no DESC
         LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()
    if message is None:
        return
    conn.execute(
        """
        INSERT INTO app.annotations (message_id, user_id, kind, content)
        VALUES (%s, %s, 'summary', %s)
        """,
        (message["id"], user_id, text),
    )


def organize_conversation(
    conn,
    conversation_id: int,
    user: dict,
    *,
    course_id: str | None,
    tag_ids: list[int],
    new_tag_names: list[str] | None = None,
    summary: str | None = None,
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
    names = [name for name in (new_tag_names or []) if name.strip()][:5]

    with conn.transaction():
        conn.execute(
            "UPDATE app.conversations SET course_id = %s WHERE id = %s",
            (target_course, conversation_id),
        )
        for name in names:
            tag_id, _ = tag_service.get_or_create_tag(conn, user["id"], name)
            selected.add(tag_id)
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
        _apply_summary(conn, conversation_id, user["id"], summary)


def bulk_organize_inbox(
    conn, conversation_ids: list[int], user: dict, *, course_id: int
) -> int:
    allowed = {row["id"] for row in editable_courses(conn, user["id"])}
    if course_id not in allowed:
        raise PermissionDenied("只能归类到自己可编辑的课程")
    if not conversation_ids:
        return 0
    row = conn.execute(
        """
        UPDATE app.conversations v
           SET course_id = %s
          FROM app.sources s
         WHERE s.id = v.source_id
           AND v.id = ANY(%s)
           AND s.owner_id = %s
           AND v.course_id IS NULL
        """,
        (course_id, sorted(set(conversation_ids)), user["id"]),
    )
    return row.rowcount


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

    params: list[int | str] = [conversation_id]
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
        "summary_text": (
            (_summary_annotation(conn, conversation_id, user["id"]) or {}).get("content", "")
            if can_edit
            else ""
        ),
    }
