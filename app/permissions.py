from __future__ import annotations

from .errors import PermissionDenied

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def get_course_role(conn, course_id: int, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT role FROM app.course_members WHERE course_id = %s AND user_id = %s",
        (course_id, user_id),
    ).fetchone()
    return row["role"] if row else None


def require_course_role(conn, course_id: int, user: dict, minimum: str = "viewer") -> str:
    role = get_course_role(conn, course_id, user["id"])
    if role is None or ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise PermissionDenied("你没有该课程的相应权限")
    return role


def require_system_admin(user: dict) -> None:
    if user["system_role"] != "admin":
        raise PermissionDenied("仅管理员可以执行此操作")


def can_access_course(conn, course_id: int, user_id: int) -> bool:
    return get_course_role(conn, course_id, user_id) is not None


def can_access_conversation(conn, conversation_id: int, user_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM app.conversations c
          JOIN app.sources s ON s.id = c.source_id
         WHERE c.id = %s
           AND (
                s.owner_id = %s
                OR (c.course_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = c.course_id AND cm.user_id = %s
                ))
           )
        """,
        (conversation_id, user_id, user_id),
    ).fetchone()
    return row is not None


def require_conversation_access(conn, conversation_id: int, user: dict) -> None:
    if not can_access_conversation(conn, conversation_id, user["id"]):
        raise PermissionDenied("你没有访问该会话的权限")


def require_conversation_edit(conn, conversation_id: int, user: dict) -> None:
    row = conn.execute(
        """
        SELECT s.owner_id, c.course_id
          FROM app.conversations c
          JOIN app.sources s ON s.id = c.source_id
         WHERE c.id = %s
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise PermissionDenied("会话不存在")
    if row["owner_id"] == user["id"]:
        return
    if row["course_id"] is not None:
        role = get_course_role(conn, row["course_id"], user["id"])
        if role in ("owner", "editor"):
            return
    raise PermissionDenied("你没有编辑该会话的权限")
