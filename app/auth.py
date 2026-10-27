from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import Depends, Request

from .db import get_conn
from .errors import AuthRequired, PermissionDenied

PBKDF2_ITERATIONS = 210_000
PBKDF2_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join(
        (
            PBKDF2_ALGO,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$")
        if algo != PBKDF2_ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise PermissionDenied("CSRF 校验失败，请刷新页面后重试")


def load_user(request: Request, conn) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    from .services.auth_service import get_user

    user = get_user(conn, int(user_id))
    if user is None or not user["is_active"]:
        request.session.clear()
        return None
    return user


def require_user(request: Request, conn=Depends(get_conn)) -> dict:
    user = load_user(request, conn)
    if user is None:
        raise AuthRequired("请先登录")
    request.state.user = user
    return user


def optional_user(request: Request, conn=Depends(get_conn)) -> dict | None:
    user = load_user(request, conn)
    request.state.user = user
    return user


def require_token(request: Request, conn=Depends(get_conn)) -> dict:
    """Bearer 采集令牌认证，不依赖浏览器会话。"""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    token = value.strip()
    if scheme.lower() != "bearer" or not token:
        raise AuthRequired("请在 Authorization: Bearer 中提供采集令牌")
    from .services.token_service import authenticate

    info = authenticate(conn, token)
    if info["system_role"] == "admin":
        raise PermissionDenied("管理员不参与聊天记录采集")
    user = {
        "id": info["user_id"],
        "username": None,
        "display_name": info["display_name"],
        "system_role": info["system_role"],
        "is_active": True,
    }
    request.state.user = user
    return user
