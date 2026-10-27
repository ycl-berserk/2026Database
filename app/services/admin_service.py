from __future__ import annotations

from ..errors import NotFoundError, ValidationError


def system_stats(conn) -> dict:
    return conn.execute(
        """
        SELECT
            (SELECT count(*) FROM app.users) AS user_count,
            (SELECT count(*) FROM app.users WHERE is_active) AS active_user_count,
            (SELECT count(*) FROM app.courses) AS course_count,
            (SELECT count(*) FROM app.course_members) AS membership_count,
            (SELECT count(*) FROM app.sources) AS source_count,
            (SELECT count(*) FROM app.conversations) AS conversation_count,
            (SELECT count(*) FROM app.messages) AS message_count,
            (SELECT count(*) FROM app.annotations) AS annotation_count,
            (SELECT count(*) FROM app.review_items) AS review_item_count,
            (SELECT count(*) FROM app.import_batches) AS import_batch_count,
            (SELECT count(*) FROM app.text2sql_log) AS t2s_log_count
        """
    ).fetchone()


def list_users(conn, keyword: str | None = None) -> list[dict]:
    keyword = (keyword or "").strip()[:50] or None
    return conn.execute(
        """
        SELECT u.id, u.username, u.display_name, u.system_role, u.is_active,
               u.created_at,
               (SELECT count(*) FROM app.course_members cm WHERE cm.user_id = u.id) AS course_count,
               (SELECT count(*) FROM app.sources s WHERE s.owner_id = u.id) AS source_count,
               (SELECT count(*) FROM app.conversations c
                  JOIN app.sources s ON s.id = c.source_id
                 WHERE s.owner_id = u.id) AS conversation_count,
               (SELECT count(*) FROM app.annotations a WHERE a.user_id = u.id) AS annotation_count
          FROM app.users u
         WHERE (%(pattern)s::text IS NULL
                OR u.username ILIKE %(pattern)s
                OR u.display_name ILIKE %(pattern)s)
         ORDER BY u.created_at, u.id
        """,
        {"pattern": f"%{keyword}%" if keyword else None},
    ).fetchall()


def recent_activity(conn, limit: int = 8) -> list[dict]:
    return conn.execute(
        """
        SELECT b.id, b.file_name, b.status, b.summary, b.created_at,
               s.platform, u.display_name
          FROM app.import_batches b
          JOIN app.sources s ON s.id = b.source_id
          JOIN app.users u ON u.id = b.user_id
         ORDER BY b.created_at DESC, b.id DESC
         LIMIT %s
        """,
        (limit,),
    ).fetchall()


def set_active(conn, user_id: int, active: bool, *, acting_user_id: int) -> dict:
    if user_id == acting_user_id:
        raise ValidationError("不能停用当前登录的管理员账号")
    row = conn.execute(
        "SELECT id, username, is_active FROM app.users WHERE id = %s", (user_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("用户不存在")
    with conn.transaction():
        conn.execute(
            "UPDATE app.users SET is_active = %s WHERE id = %s", (active, user_id)
        )
    return row
