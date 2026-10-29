"""桌面个人模式：首个账号是管理员，也必须能使用学习功能。"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app import web as web_module
from app.config import settings as real_settings
from app.db import get_conn
from app.main import app
from app.routers import dashboard as dashboard_router

pytestmark = pytest.mark.integration


def _login_admin(client: TestClient) -> None:
    html = client.get("/login").text
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "登录页缺少 CSRF 令牌"
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "Passw0rd!",
            "csrf_token": match.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.fixture
def client(conn):
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def desktop(monkeypatch):
    desktop_settings = replace(real_settings, desktop_mode=True)
    for module in (auth_module, web_module, dashboard_router):
        monkeypatch.setattr(module, "settings", desktop_settings)


def test_demo_admin_stays_aggregate_only(client, base):
    _login_admin(client)
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "系统概览" in dashboard.text
    assert "用户与系统" in dashboard.text
    assert client.get("/courses").status_code == 403
    assert client.get("/search", params={"q": "索引"}).status_code == 403


def test_desktop_admin_can_learn(client, base, desktop):
    _login_admin(client)
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "学习概览" in dashboard.text
    assert "用户与系统" in dashboard.text
    assert "导入记录" in dashboard.text
    assert client.get("/courses").status_code == 200
    assert client.get("/tokens").status_code == 200
    assert client.get("/search", params={"q": "索引"}).status_code == 200
