from __future__ import annotations

import base64
import json
import re

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.config import settings
from app.db import get_conn
from app.main import app
from app.security import login_throttle


def _csrf_token(client: TestClient) -> str:
    html = client.get("/login").text
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "登录页缺少 CSRF 令牌"
    return match.group(1)


def _login(client: TestClient, token: str, password: str):
    return client.post(
        "/login",
        data={"username": "alice", "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def test_login_throttle_blocks_after_failures_and_recovers(conn, base):
    login_throttle.reset()
    app.dependency_overrides[get_conn] = lambda: conn
    client = TestClient(app)
    try:
        token = _csrf_token(client)
        for _ in range(settings.login_max_attempts):
            assert _login(client, token, "wrong-password").status_code == 401

        blocked = _login(client, token, "Passw0rd!")
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0
        assert "频繁" in blocked.text

        login_throttle.reset()
        ok = _login(client, token, "Passw0rd!")
        assert ok.status_code == 303
        assert ok.headers["location"] == "/dashboard"

        cookie = ok.cookies.get("ls_session")
        payload = TimestampSigner(settings.session_secret).unsign(
            cookie, max_age=settings.session_max_age
        )
        session = json.loads(base64.b64decode(payload))
        assert session["user_id"] == base["users"]["alice"]
        assert isinstance(session["issued_at"], int)
        assert session["nonce"]
    finally:
        app.dependency_overrides.clear()
        login_throttle.reset()
