from __future__ import annotations

import hashlib
import secrets

from ..errors import AuthRequired, NotFoundError, RateLimited, ValidationError

RATE_LIMIT_PER_MINUTE = 60


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def create_token(conn, user_id: int, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValidationError("请填写令牌用途")
    if len(name) > 60:
        raise ValidationError("令牌名称不能超过 60 字符")
    token = "ls_" + secrets.token_urlsafe(32)
    row = conn.execute(
        """
        INSERT INTO app.api_tokens (user_id, name, token_prefix, token_hash)
        VALUES (%s, %s, %s, %s)
        RETURNING id, name, token_prefix, created_at
        """,
        (user_id, name, token[:11], _hash(token)),
    ).fetchone()
    return {**row, "token": token}


def list_tokens(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT id, name, token_prefix, created_at, last_used_at, revoked_at
          FROM app.api_tokens
         WHERE user_id = %s
         ORDER BY (revoked_at IS NULL) DESC, created_at DESC
        """,
        (user_id,),
    ).fetchall()


def revoke_token(conn, token_id: int, user_id: int) -> None:
    row = conn.execute(
        """
        UPDATE app.api_tokens
           SET revoked_at = now()
         WHERE id = %s AND user_id = %s AND revoked_at IS NULL
         RETURNING id
        """,
        (token_id, user_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("令牌不存在或已撤销")


def authenticate(conn, token: str) -> dict:
    token = (token or "").strip()
    if len(token) < 20:
        raise AuthRequired("采集令牌无效或已撤销")
    row = conn.execute(
        """
        UPDATE app.api_tokens t
           SET last_used_at = now(),
               window_started_at = CASE
                   WHEN t.window_started_at > now() - interval '1 minute'
                   THEN t.window_started_at ELSE now() END,
               window_count = CASE
                   WHEN t.window_started_at > now() - interval '1 minute'
                   THEN t.window_count + 1 ELSE 1 END
          FROM app.users u
         WHERE t.token_hash = %s
           AND t.revoked_at IS NULL
           AND u.id = t.user_id
           AND u.is_active
        RETURNING t.id AS token_id, t.user_id, t.window_count,
                  u.system_role, u.display_name
        """,
        (_hash(token),),
    ).fetchone()
    if row is None:
        raise AuthRequired("采集令牌无效或已撤销")
    if row["window_count"] > RATE_LIMIT_PER_MINUTE:
        raise RateLimited("采集令牌每分钟最多 60 次请求，请稍后再试")
    return row
