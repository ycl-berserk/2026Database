from __future__ import annotations

from psycopg import Connection


def set_actor(conn: Connection, user_id: int | None) -> None:
    """在当前事务内标记操作者，供 app.audit_row_change 触发器读取。"""
    conn.execute(
        "SELECT set_config('app.actor_user_id', %s, true)",
        ("" if user_id is None else str(user_id),),
    )
