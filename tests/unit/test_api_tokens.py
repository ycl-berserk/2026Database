from __future__ import annotations

import pytest
from starlette.requests import Request

from app.auth import require_token
from app.errors import AuthRequired, PermissionDenied
from app.services import token_service


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ingest",
            "headers": raw,
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
        }
    )


def _token_info(role: str) -> dict:
    return {
        "token_id": 1,
        "user_id": 7,
        "window_count": 1,
        "system_role": role,
        "display_name": "测试用户",
    }


def test_require_token_needs_bearer_header():
    with pytest.raises(AuthRequired):
        require_token(_request(), conn=object())
    with pytest.raises(AuthRequired):
        require_token(_request({"Authorization": "Token ls_abcdef"}), conn=object())


def test_require_token_denies_admin(monkeypatch):
    monkeypatch.setattr(
        token_service,
        "authenticate",
        lambda conn, token: _token_info("admin"),
    )
    with pytest.raises(PermissionDenied):
        require_token(_request({"Authorization": "Bearer ls_abcdef"}), conn=object())


def test_require_token_returns_student_context(monkeypatch):
    monkeypatch.setattr(
        token_service,
        "authenticate",
        lambda conn, token: _token_info("student"),
    )
    user = require_token(_request({"Authorization": "Bearer ls_abcdef"}), conn=object())
    assert user["id"] == 7
    assert user["system_role"] == "student"
    assert user["display_name"] == "测试用户"


def test_ingest_and_tokens_routes_are_registered():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/tokens" in paths
    assert "/tokens/{token_id}/revoke" in paths
    assert "/api/ingest" in paths
