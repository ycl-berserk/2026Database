from __future__ import annotations

from typing import Any

from ..errors import ValidationError


def get_user(conn, user_id: int) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT id, username, display_name, system_role, is_active, created_at "
        "FROM app.users WHERE id = %s",
        (user_id,),
    ).fetchone()


def authenticate(conn, username: str, password: str) -> dict[str, Any] | None:
    from ..auth import verify_password

    row = conn.execute(
        "SELECT id, username, password_hash, display_name, system_role, is_active "
        "FROM app.users WHERE lower(username) = lower(%s)",
        (username.strip(),),
    ).fetchone()
    if row is None or not row["is_active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "system_role": row["system_role"],
    }


def create_user(
    conn,
    username: str,
    password: str,
    display_name: str,
    system_role: str = "student",
) -> dict[str, Any]:
    from ..auth import hash_password

    username = username.strip()
    display_name = display_name.strip()
    if len(password) < 8:
        raise ValidationError("密码至少 8 位")
    row = conn.execute(
        "INSERT INTO app.users (username, password_hash, display_name, system_role) "
        "VALUES (%s, %s, %s, %s) RETURNING id, username, display_name, system_role",
        (username, hash_password(password), display_name, system_role),
    ).fetchone()
    return row
