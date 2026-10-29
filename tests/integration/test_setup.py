from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app
from app.routers import setup as setup_router

pytestmark = pytest.mark.integration

FORM = {
    "username": "localadmin",
    "display_name": "本机管理员",
    "password": "Passw0rd!",
    "password_confirm": "Passw0rd!",
}


def _client(conn):
    app.dependency_overrides[get_conn] = lambda: conn
    return TestClient(app)


def _csrf(client: TestClient) -> str:
    html = client.get("/setup").text
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "设置页缺少 CSRF 令牌"
    return match.group(1)


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setattr(
        setup_router, "settings", SimpleNamespace(desktop_mode=True)
    )


def test_setup_is_hidden_outside_desktop_mode(conn):
    client = _client(conn)
    try:
        assert client.get("/setup").status_code == 404
        response = client.post("/setup", data=FORM)
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_setup_creates_admin_and_then_closes(conn, desktop):
    client = _client(conn)
    try:
        token = _csrf(client)
        response = client.post(
            "/setup", data={**FORM, "csrf_token": token}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"
        assert response.cookies.get("ls_session")

        user = conn.execute(
            "SELECT username, display_name, system_role FROM app.users"
        ).fetchone()
        assert user["username"] == "localadmin"
        assert user["display_name"] == "本机管理员"
        assert user["system_role"] == "admin"

        assert client.get("/setup", follow_redirects=False).status_code == 303
    finally:
        app.dependency_overrides.clear()


def test_setup_validates_password_and_username(conn, desktop):
    client = _client(conn)
    try:
        token = _csrf(client)
        mismatch = client.post(
            "/setup",
            data={
                **FORM,
                "password_confirm": "different",
                "csrf_token": token,
            },
        )
        assert mismatch.status_code == 400
        assert "不一致" in mismatch.text

        short_name = client.post(
            "/setup",
            data={**FORM, "username": "ab", "csrf_token": token},
        )
        assert short_name.status_code == 400
        assert conn.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"] == 0
    finally:
        app.dependency_overrides.clear()
