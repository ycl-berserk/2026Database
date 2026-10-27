from __future__ import annotations

import re

import psycopg
from psycopg import errors

from ..errors import ConflictError, NotFoundError, ValidationError

KINDS = ("knowledge_point", "topic", "custom")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_COLORS = ["#2f5d4e", "#a8432c", "#a6771f", "#4a6fa5", "#7a5b8f", "#5b7d3a"]


def list_tags(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT t.id, t.name, t.kind, t.color, t.created_at,
               (SELECT count(DISTINCT ct.conversation_id)
                  FROM app.conversation_tags ct
                  JOIN app.conversations v ON v.id = ct.conversation_id
                  JOIN app.sources s ON s.id = v.source_id
                 WHERE ct.tag_id = t.id
                   AND (
                        s.owner_id = %(uid)s
                        OR (v.course_id IS NOT NULL AND EXISTS (
                            SELECT 1 FROM app.course_members cm
                             WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
                        ))
                   )) AS usage_count
          FROM app.tags t
         WHERE t.owner_id = %(uid)s
         ORDER BY usage_count DESC, t.name
        """,
        {"uid": user_id},
    ).fetchall()


def _validate(name: str, kind: str, color: str | None) -> tuple[str, str, str | None]:
    name = (name or "").strip()
    if not name:
        raise ValidationError("标签名称不能为空")
    if len(name) > 40:
        raise ValidationError("标签名称不能超过 40 个字符")
    if kind not in KINDS:
        raise ValidationError("标签类型无效")
    color = (color or "").strip() or None
    if color and not COLOR_RE.match(color):
        raise ValidationError("颜色必须是 #RRGGBB 格式")
    return name, kind, color


def create_tag(conn, user_id: int, name: str, kind: str, color: str | None) -> int:
    name, kind, color = _validate(name, kind, color)
    if color is None:
        color = DEFAULT_COLORS[sum(name.encode("utf-8")) % len(DEFAULT_COLORS)]
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO app.tags (owner_id, name, kind, color) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (user_id, name, kind, color),
            ).fetchone()
    except errors.UniqueViolation as exc:
        raise ConflictError("你已经创建过同名标签") from exc
    return row["id"]


def update_tag(conn, tag_id: int, user_id: int, name: str, kind: str, color: str | None) -> None:
    _owner_tag(conn, tag_id, user_id)
    name, kind, color = _validate(name, kind, color)
    try:
        with conn.transaction():
            conn.execute(
                "UPDATE app.tags SET name = %s, kind = %s, color = %s "
                "WHERE id = %s AND owner_id = %s",
                (name, kind, color, tag_id, user_id),
            )
    except errors.UniqueViolation as exc:
        raise ConflictError("你已经创建过同名标签") from exc


def delete_tag(conn, tag_id: int, user_id: int) -> None:
    _owner_tag(conn, tag_id, user_id)
    with conn.transaction():
        conn.execute("DELETE FROM app.tags WHERE id = %s AND owner_id = %s", (tag_id, user_id))


def _owner_tag(conn, tag_id: int, user_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM app.tags WHERE id = %s AND owner_id = %s", (tag_id, user_id)
    ).fetchone()
    if row is None:
        raise NotFoundError("标签不存在")
