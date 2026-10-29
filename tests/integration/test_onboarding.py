from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import settings as real_settings
from app.db import get_conn
from app.main import app
from app.routers import auth as auth_router
from app.services import onboarding_service

pytestmark = pytest.mark.integration


@pytest.fixture
def client(conn):
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def _csrf(client: TestClient, path: str = "/login") -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, f"{path} 缺少 CSRF 令牌"
    return match.group(1)


def _login(client: TestClient, username: str, password: str = "Passw0rd!", **extra):
    token = _csrf(client)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token, **extra},
        follow_redirects=False,
    )


def test_fresh_user_starts_once_and_stays_pending(conn, base):
    bob_id = base["users"]["bob"]
    state = onboarding_service.progress(conn, bob_id)
    assert state["completed_count"] == 0
    assert state["status"] == "pending"
    assert state["stored_status"] is None
    assert [step["key"] for step in state["steps"]] == [
        "course",
        "import",
        "annotate",
        "review",
    ]

    assert onboarding_service.ensure_started(conn, bob_id) is True
    assert onboarding_service.progress(conn, bob_id)["stored_status"] == "pending"
    assert onboarding_service.ensure_started(conn, bob_id) is False


def test_existing_progress_is_completed_without_stored_row(conn, base):
    alice_id = base["users"]["alice"]
    state = onboarding_service.progress(conn, alice_id)
    assert state["completed_count"] == 4
    assert state["status"] == "completed"
    assert state["stored_status"] is None

    assert onboarding_service.ensure_started(conn, alice_id) is False
    row = conn.execute(
        "SELECT 1 FROM app.user_onboarding WHERE user_id = %s", (alice_id,)
    ).fetchone()
    assert row is None


def test_partial_progress_is_not_forced_into_onboarding(conn, base):
    bob_id = base["users"]["bob"]
    conn.execute(
        "INSERT INTO app.annotations (message_id, user_id, kind, content) "
        "VALUES (%s, %s, 'note', '先记一条')",
        (base["messages"]["os-1"], bob_id),
    )
    state = onboarding_service.progress(conn, bob_id)
    assert state["completed_count"] == 1
    assert state["status"] == "pending"
    assert onboarding_service.ensure_started(conn, bob_id) is False


def test_skip_and_restart_round_trip(conn, base):
    bob_id = base["users"]["bob"]
    onboarding_service.mark_skipped(conn, bob_id)
    assert onboarding_service.progress(conn, bob_id)["status"] == "skipped"

    onboarding_service.restart(conn, bob_id)
    state = onboarding_service.progress(conn, bob_id)
    assert state["status"] == "pending"
    assert state["stored_status"] == "pending"


def test_completion_is_derived_from_real_activity(conn, base):
    bob_id = base["users"]["bob"]
    with conn.transaction():
        course = conn.execute(
            "INSERT INTO app.courses (code, name, semester, created_by) "
            "VALUES ('NEW', '新课程', '2026 秋', %s) RETURNING id",
            (bob_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO app.course_members (course_id, user_id, role) "
            "VALUES (%s, %s, 'owner')",
            (course["id"], bob_id),
        )
    source = conn.execute(
        "INSERT INTO app.sources (owner_id, platform, account_label) "
        "VALUES (%s, 'qoder', 'bob@qoder') RETURNING id",
        (bob_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO app.conversations (source_id, external_id, title) "
        "VALUES (%s, 'bob-1', '自己的会话')",
        (source["id"],),
    )
    annotation = conn.execute(
        "INSERT INTO app.annotations (message_id, user_id, kind, content) "
        "VALUES (%s, %s, 'note', '自己的批注') RETURNING id",
        (base["messages"]["os-1"], bob_id),
    ).fetchone()
    conn.execute(
        "INSERT INTO app.review_items (user_id, annotation_id, due_date, interval_days) "
        "VALUES (%s, %s, current_date, 1)",
        (bob_id, annotation["id"]),
    )

    state = onboarding_service.progress(conn, bob_id)
    assert state["completed_count"] == 4
    assert state["status"] == "completed"
    assert state["stored_status"] is None
    assert onboarding_service.ensure_started(conn, bob_id) is False


def test_first_login_redirects_zero_progress_user_to_welcome(client, conn, base):
    bob_id = base["users"]["bob"]
    response = _login(client, "bob")
    assert response.status_code == 303
    assert response.headers["location"] == "/welcome"
    assert onboarding_service.progress(conn, bob_id)["stored_status"] == "pending"

    again = _login(client, "bob")
    assert again.headers["location"] == "/dashboard"


def test_first_login_respects_explicit_next(client, conn, base):
    bob_id = base["users"]["bob"]
    response = _login(client, "bob", next="/inbox")
    assert response.status_code == 303
    assert response.headers["location"] == "/inbox"
    assert onboarding_service.progress(conn, bob_id)["stored_status"] is None


def test_admin_outside_desktop_mode_is_not_redirected(client, base):
    response = _login(client, "admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_desktop_admin_first_login_starts_onboarding(client, base, monkeypatch):
    monkeypatch.setattr(
        auth_router, "settings", replace(real_settings, desktop_mode=True)
    )
    response = _login(client, "admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/welcome"


def test_welcome_page_skip_and_restart_flow(client, conn, base):
    bob_id = base["users"]["bob"]
    _login(client, "bob")

    page = client.get("/welcome")
    assert page.status_code == 200
    assert "先完成这四步" in page.text
    assert "0<span>/4</span>" in page.text

    skip = client.post(
        "/welcome/skip",
        data={"csrf_token": _csrf(client, "/welcome")},
        follow_redirects=False,
    )
    assert skip.status_code == 303
    assert skip.headers["location"] == "/dashboard"
    assert onboarding_service.progress(conn, bob_id)["status"] == "skipped"

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "新手指南已收起" in dashboard.text

    restart = client.post(
        "/welcome/restart",
        data={"csrf_token": _csrf(client, "/welcome")},
        follow_redirects=False,
    )
    assert restart.status_code == 303
    assert restart.headers["location"] == "/welcome"
    assert onboarding_service.progress(conn, bob_id)["status"] == "pending"


def test_welcome_is_closed_to_aggregate_only_admin(client, base):
    response = _login(client, "admin")
    assert response.headers["location"] == "/dashboard"
    assert client.get("/welcome").status_code == 403
