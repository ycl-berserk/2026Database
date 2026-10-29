from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_token_page_documents_one_time_secret_and_ingest_contract():
    body = _read(TEMPLATES / "tokens" / "list.html")
    assert "只显示这一次" in body
    assert "data-copy=\"{{ new_token }}\"" in body
    assert "Authorization: Bearer" in body
    assert "/api/ingest" in body
    assert "每分钟最多" in body
    assert 'action="/tokens/{{ token.id }}/revoke"' in body

    nav = _read(TEMPLATES / "base.html")
    assert 'href="/tokens"' in nav

    css = _read(STATIC / "app.css")
    assert ".token-reveal" in css
    assert ".token-copy" in css


def test_login_exposes_one_click_demo_accounts():
    login = _read(TEMPLATES / "login.html")
    assert "data-demo-login" in login
    assert 'data-username="alice"' in login
    assert 'data-password="Bob123!"' in login
    assert "{% if demo_mode %}" in login


def test_new_user_onboarding_has_next_steps():
    dashboard = _read(TEMPLATES / "dashboard.html")
    assert "onboarding.status == 'pending'" in dashboard
    assert 'href="/welcome"' in dashboard
    assert "onboarding.completed_count" in dashboard

    welcome = _read(TEMPLATES / "welcome.html")
    assert "先完成这四步" in welcome
    for href in ("/courses/new", "/imports", "/imports?mode=paste", "/inbox"):
        assert f"'{href}'" in welcome or f'href="{href}"' in welcome


def test_flash_and_toast_are_announced_and_dismissible():
    base = _read(TEMPLATES / "base.html")
    assert 'aria-live="polite"' in base
    assert "flash-close" in base

    script = _read(STATIC / "app.js")
    assert 'setAttribute("role", "status")' in script
    assert "toast-action" in script
    assert "data-flash-category" in script


def test_destructive_actions_support_five_second_undo():
    script = _read(STATIC / "app.js")
    assert "data-undo-submit" in script
    assert "5000" in script
    assert "sendBeacon" in script

    for name in ("annotations/list.html", "reviews/list.html", "tags/list.html"):
        body = _read(TEMPLATES / name)
        assert "data-undo-submit=" in body, name
        assert "data-undo-item" in body, name

    css = _read(STATIC / "app.css")
    assert ".is-removing" in css
    assert ".toast-action" in css


def test_global_search_dialog_has_shortcuts_and_memory():
    base = _read(TEMPLATES / "base.html")
    assert "data-search-dialog" in base
    assert "data-search-open" in base
    assert 'action="/search"' in base

    script = _read(STATIC / "app.js")
    assert "ls-recent-searches" in script
    assert "ls-recent-visits" in script
    assert 'event.key === "/"' in script
    assert 'toLowerCase() === "k"' in script

    page = _read(TEMPLATES / "search.html")
    assert "全局搜索" in page
    assert "data.annotations" in page
    assert "highlight(q)" in page


def test_unknown_page_explains_reason_and_recovery():
    from app.main import app

    client = TestClient(app)
    try:
        response = client.get("/missing-page")
    finally:
        client.close()
    assert response.status_code == 404
    assert "页面不存在" in response.text
    assert "可能的原因与下一步" in response.text
    assert "/missing-page" in response.text
    assert "浏览我的会话" in response.text


def test_api_and_static_errors_stay_machine_readable():
    from app.main import app

    client = TestClient(app)
    try:
        api = client.get("/api/missing")
        static = client.get("/static/missing.css")
    finally:
        client.close()
    assert api.status_code == 404
    assert api.headers["content-type"].startswith("application/json")
    assert static.status_code == 404
    assert static.headers["content-type"].startswith("text/plain")


def test_server_error_page_has_generic_recovery_copy():
    from app.main import ERROR_HINTS

    assert 500 in ERROR_HINTS
    error = _read(TEMPLATES / "error.html")
    assert "返回上一页" in error
    assert "error_hint" in error
    assert "error_path" in error


def test_static_assets_are_compressed_versioned_and_cacheable():
    from app.main import app

    client = TestClient(app)
    try:
        plain = client.get("/static/app.css")
        versioned = client.get("/static/app.css?v=test")
        page = client.get("/missing-page")
    finally:
        client.close()

    assert plain.status_code == 200
    assert plain.headers["cache-control"] == "public, max-age=300"
    assert "gzip" in plain.headers.get("content-encoding", "")
    assert versioned.headers["cache-control"].endswith("immutable")
    assert page.headers["cache-control"] == "no-store"
    assert "/static/app.css?v=" in page.text
    assert "/static/app.js?v=" in page.text


def test_frontend_budget_and_reduced_motion():
    assert (STATIC / "app.js").stat().st_size < 100 * 1024
    assert (STATIC / "app.css").stat().st_size < 100 * 1024
    css = _read(STATIC / "app.css")
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
