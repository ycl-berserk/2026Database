from __future__ import annotations

from collections.abc import Mapping

from psycopg import errors

from ..audit import set_actor
from ..errors import ConflictError, NotFoundError, ValidationError
from ..permissions import require_course_role


def list_courses(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT c.id, c.code, c.name, c.semester, c.instructor, c.description,
               c.created_at, cm.role,
               (SELECT count(*) FROM app.conversations v WHERE v.course_id = c.id) AS conversation_count,
               (SELECT count(*) FROM app.messages m
                  JOIN app.conversations v ON v.id = m.conversation_id
                 WHERE v.course_id = c.id) AS message_count,
               (SELECT count(*) FROM app.course_members x WHERE x.course_id = c.id) AS member_count
          FROM app.courses c
          JOIN app.course_members cm ON cm.course_id = c.id AND cm.user_id = %s
         ORDER BY c.created_at DESC, c.id DESC
        """,
        (user_id,),
    ).fetchall()


def get_course(conn, course_id: int) -> dict:
    row = conn.execute(
        """
        SELECT c.id, c.code, c.name, c.semester, c.instructor, c.description,
               c.created_at, u.display_name AS creator_name
          FROM app.courses c
          JOIN app.users u ON u.id = c.created_by
         WHERE c.id = %s
        """,
        (course_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("课程不存在")
    return row


def course_detail(conn, course_id: int, user: dict) -> dict:
    course = get_course(conn, course_id)
    role = require_course_role(conn, course_id, user)

    members = conn.execute(
        """
        SELECT u.id, u.username, u.display_name, u.system_role, cm.role, cm.joined_at
          FROM app.course_members cm
          JOIN app.users u ON u.id = cm.user_id
         WHERE cm.course_id = %s
         ORDER BY CASE cm.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END,
                  u.display_name
        """,
        (course_id,),
    ).fetchall()

    counts = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM app.conversations WHERE course_id = %s) AS conversation_count,
            (SELECT count(*) FROM app.messages m
               JOIN app.conversations v ON v.id = m.conversation_id
              WHERE v.course_id = %s) AS message_count,
            (SELECT count(*) FROM app.annotations a
               JOIN app.messages m ON m.id = a.message_id
               JOIN app.conversations v ON v.id = m.conversation_id
              WHERE v.course_id = %s) AS annotation_count
        """,
        (course_id, course_id, course_id),
    ).fetchone()

    conversations = conn.execute(
        """
        SELECT v.id, v.title, v.started_at, s.platform,
               count(m.id) AS message_count,
               max(m.created_at) AS last_message_at
          FROM app.conversations v
          JOIN app.sources s ON s.id = v.source_id
          LEFT JOIN app.messages m ON m.conversation_id = v.id
         WHERE v.course_id = %s
         GROUP BY v.id, v.title, v.started_at, s.platform
         ORDER BY coalesce(max(m.created_at), v.started_at) DESC NULLS LAST, v.id DESC
         LIMIT 12
        """,
        (course_id,),
    ).fetchall()

    tags = conn.execute(
        """
        SELECT t.id, t.name, t.kind, t.color, count(ct.conversation_id) AS usage_count
          FROM app.conversation_tags ct
          JOIN app.tags t ON t.id = ct.tag_id
          JOIN app.conversations v ON v.id = ct.conversation_id
         WHERE v.course_id = %s
         GROUP BY t.id, t.name, t.kind, t.color
         ORDER BY usage_count DESC, t.name
         LIMIT 12
        """,
        (course_id,),
    ).fetchall()

    return {
        "course": course,
        "role": role,
        "members": members,
        "counts": counts,
        "conversations": conversations,
        "tags": tags,
    }


def require_owner(conn, course_id: int, user: dict) -> None:
    require_course_role(conn, course_id, user, "owner")


def _course_values(form: Mapping[str, object]) -> dict:
    def text(key: str) -> str:
        value = form.get(key)
        return value.strip() if isinstance(value, str) else ""

    values = {
        "code": text("code"),
        "name": text("name"),
        "semester": text("semester"),
        "instructor": text("instructor") or None,
        "description": text("description") or None,
    }
    if not values["code"] or not values["name"] or not values["semester"]:
        raise ValidationError("课程编号、名称和学期为必填项")
    if len(values["code"]) > 20 or len(values["name"]) > 100 or len(values["semester"]) > 20:
        raise ValidationError("课程编号、名称或学期超出长度限制")
    return values


def create_course(conn, form: Mapping[str, object], user_id: int) -> int:
    values = _course_values(form)
    try:
        with conn.transaction():
            set_actor(conn, user_id)
            course_id = conn.execute(
                """
                INSERT INTO app.courses (code, name, semester, instructor, description, created_by)
                VALUES (%(code)s, %(name)s, %(semester)s, %(instructor)s, %(description)s, %(created_by)s)
                RETURNING id
                """,
                {**values, "created_by": user_id},
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO app.course_members (course_id, user_id, role) VALUES (%s, %s, 'owner')",
                (course_id, user_id),
            )
    except errors.UniqueViolation as exc:
        raise ConflictError("你在该学期已经创建过同编号的课程") from exc
    except errors.CheckViolation as exc:
        raise ValidationError("课程字段不符合格式要求") from exc
    return course_id


def update_course(conn, course_id: int, form: Mapping[str, object], user: dict) -> None:
    require_course_role(conn, course_id, user, "owner")
    values = _course_values(form)
    try:
        with conn.transaction():
            set_actor(conn, user["id"])
            conn.execute(
                """
                UPDATE app.courses
                   SET code = %(code)s, name = %(name)s, semester = %(semester)s,
                       instructor = %(instructor)s, description = %(description)s
                 WHERE id = %(id)s
                """,
                {**values, "id": course_id},
            )
    except errors.UniqueViolation as exc:
        raise ConflictError("你在该学期已经创建过同编号的课程") from exc
    except errors.CheckViolation as exc:
        raise ValidationError("课程字段不符合格式要求") from exc


def delete_course(conn, course_id: int, user: dict) -> None:
    require_course_role(conn, course_id, user, "owner")
    with conn.transaction():
        set_actor(conn, user["id"])
        conn.execute("DELETE FROM app.courses WHERE id = %s", (course_id,))


def member_candidates(conn, course_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT u.id, u.username, u.display_name
          FROM app.users u
         WHERE u.is_active
           AND NOT EXISTS (
                SELECT 1 FROM app.course_members cm
                 WHERE cm.course_id = %s AND cm.user_id = u.id
           )
         ORDER BY u.username
         LIMIT 50
        """,
        (course_id,),
    ).fetchall()


def add_member(conn, course_id: int, username: str, role: str, user: dict) -> None:
    require_course_role(conn, course_id, user, "owner")
    if role not in ("editor", "viewer"):
        raise ValidationError("成员角色只能是 editor 或 viewer")
    target = conn.execute(
        "SELECT id, display_name FROM app.users WHERE lower(username) = lower(%s) AND is_active",
        (username.strip(),),
    ).fetchone()
    if target is None:
        raise ValidationError("找不到该用户，或账号已停用")
    if target["id"] == user["id"]:
        raise ValidationError("不能把自己重复加入课程")
    try:
        with conn.transaction():
            set_actor(conn, user["id"])
            conn.execute(
                "INSERT INTO app.course_members (course_id, user_id, role) VALUES (%s, %s, %s)",
                (course_id, target["id"], role),
            )
    except errors.UniqueViolation as exc:
        raise ConflictError("该用户已经是课程成员") from exc


def set_member_role(conn, course_id: int, member_id: int, role: str, user: dict) -> None:
    require_course_role(conn, course_id, user, "owner")
    if role not in ("editor", "viewer"):
        raise ValidationError("成员角色只能是 editor 或 viewer")
    with conn.transaction():
        set_actor(conn, user["id"])
        cur = conn.execute(
            "UPDATE app.course_members SET role = %s "
            "WHERE course_id = %s AND user_id = %s AND role <> 'owner'",
            (role, course_id, member_id),
        )
        if cur.rowcount == 0:
            raise ValidationError("成员不存在，或不能修改所有者角色")


def remove_member(conn, course_id: int, member_id: int, user: dict) -> None:
    require_course_role(conn, course_id, user, "owner")
    if member_id == user["id"]:
        raise ValidationError("不能移除课程所有者")
    with conn.transaction():
        set_actor(conn, user["id"])
        cur = conn.execute(
            "DELETE FROM app.course_members "
            "WHERE course_id = %s AND user_id = %s AND role <> 'owner'",
            (course_id, member_id),
        )
        if cur.rowcount == 0:
            raise ValidationError("成员不存在，或不能移除所有者")
