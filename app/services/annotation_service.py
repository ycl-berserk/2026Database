from __future__ import annotations

from ..errors import NotFoundError, PermissionDenied, ValidationError
from .conversation_service import make_snippet

KINDS = ("note", "question", "correction", "summary")
STATUSES = ("open", "resolved")
PAGE_SIZE = 10


def _message_access(conn, message_id: int, user_id: int) -> dict:
    row = conn.execute(
        """
        SELECT m.id, m.conversation_id, v.course_id, v.title AS conversation_title,
               s.owner_id,
               (s.owner_id = %(uid)s OR EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id
                       AND cm.user_id = %(uid)s
                       AND cm.role IN ('owner', 'editor')
               )) AS can_edit
          FROM app.messages m
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
         WHERE m.id = %(mid)s
           AND (
                s.owner_id = %(uid)s
                OR (v.course_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
                ))
           )
        """,
        {"uid": user_id, "mid": message_id},
    ).fetchone()
    if row is None:
        raise NotFoundError("消息不存在，或你没有访问权限")
    if not row["can_edit"]:
        raise PermissionDenied("只有会话导入者或课程所有者/编辑者可以添加批注")
    return row


def _annotation(conn, annotation_id: int, user_id: int) -> dict:
    row = conn.execute(
        """
        SELECT a.id, a.message_id, a.user_id, a.kind, a.content, a.status,
               v.id AS conversation_id, v.title AS conversation_title
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
         WHERE a.id = %s
        """,
        (annotation_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("批注不存在")
    if row["user_id"] != user_id:
        raise PermissionDenied("只能修改自己创建的批注")
    return row


def _validate(kind: str, content: str) -> tuple[str, str]:
    if kind not in KINDS:
        raise ValidationError("批注类型无效")
    content = (content or "").strip()
    if not content:
        raise ValidationError("批注内容不能为空")
    if len(content) > 4000:
        raise ValidationError("批注内容不能超过 4000 字符")
    return kind, content


def create_annotation(conn, message_id: int, user: dict, kind: str, content: str) -> dict:
    _message_access(conn, message_id, user["id"])
    kind, content = _validate(kind, content)
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO app.annotations (message_id, user_id, kind, content)
            VALUES (%s, %s, %s, %s)
            RETURNING id, message_id, kind, content, status, created_at
            """,
            (message_id, user["id"], kind, content),
        ).fetchone()
    return row


def update_annotation(
    conn, annotation_id: int, user: dict, kind: str, content: str, status: str
) -> None:
    _annotation(conn, annotation_id, user["id"])
    kind, content = _validate(kind, content)
    if status not in STATUSES:
        raise ValidationError("批注状态无效")
    with conn.transaction():
        conn.execute(
            "UPDATE app.annotations SET kind = %s, content = %s, status = %s "
            "WHERE id = %s AND user_id = %s",
            (kind, content, status, annotation_id, user["id"]),
        )


def delete_annotation(conn, annotation_id: int, user: dict) -> None:
    _annotation(conn, annotation_id, user["id"])
    with conn.transaction():
        conn.execute(
            "DELETE FROM app.annotations WHERE id = %s AND user_id = %s",
            (annotation_id, user["id"]),
        )


def add_to_review(conn, annotation_id: int, user: dict) -> bool:
    row = conn.execute(
        """
        SELECT a.id,
               (s.owner_id = %(uid)s OR EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id
                       AND cm.user_id = %(uid)s
                       AND cm.role IN ('owner', 'editor')
               )) AS can_edit
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
          JOIN app.sources s ON s.id = v.source_id
         WHERE a.id = %(aid)s
           AND (
                s.owner_id = %(uid)s
                OR (v.course_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
                ))
           )
        """,
        {"uid": user["id"], "aid": annotation_id},
    ).fetchone()
    if row is None:
        raise NotFoundError("批注不存在，或你没有访问权限")
    if not row["can_edit"]:
        raise PermissionDenied("当前课程角色只能浏览，不能建立复习项")
    with conn.transaction():
        created = conn.execute(
            """
            INSERT INTO app.review_items (user_id, annotation_id, due_date, interval_days)
            VALUES (%s, %s, current_date, 1)
            ON CONFLICT (user_id, annotation_id) DO NOTHING
            RETURNING id
            """,
            (user["id"], annotation_id),
        ).fetchone()
    return created is not None


def list_annotations(
    conn,
    user_id: int,
    *,
    status: str | None = None,
    kind: str | None = None,
    page: int = 1,
) -> dict:
    clauses = ["a.user_id = %(uid)s"]
    params: dict = {"uid": user_id, "limit": PAGE_SIZE}
    if status in STATUSES:
        clauses.append("a.status = %(status)s")
        params["status"] = status
    if kind in KINDS:
        clauses.append("a.kind = %(kind)s")
        params["kind"] = kind

    page = max(1, page)
    params["offset"] = (page - 1) * PAGE_SIZE
    rows = conn.execute(
        f"""
        SELECT a.id, a.kind, a.content, a.status, a.created_at, a.updated_at,
               m.id AS message_id, m.seq_no, m.content AS message_content, m.sender,
               v.id AS conversation_id, v.title AS conversation_title,
               c.code AS course_code,
               ri.id AS review_id, ri.status AS review_status, ri.due_date,
               count(*) OVER() AS total
          FROM app.annotations a
          JOIN app.messages m ON m.id = a.message_id
          JOIN app.conversations v ON v.id = m.conversation_id
          LEFT JOIN app.courses c ON c.id = v.course_id
          LEFT JOIN app.review_items ri
                 ON ri.annotation_id = a.id AND ri.user_id = %(uid)s
         WHERE {' AND '.join(clauses)}
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    total = rows[0]["total"] if rows else 0
    for row in rows:
        row["snippet"] = make_snippet(row["message_content"], None, 110)
    return {
        "rows": rows,
        "status": status or "",
        "kind": kind or "",
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
        "total_pages": (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 0,
    }
