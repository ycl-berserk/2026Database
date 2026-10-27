from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_login_exposes_one_click_demo_accounts():
    login = _read(TEMPLATES / "login.html")
    assert "data-demo-login" in login
    assert 'data-username="alice"' in login
    assert 'data-password="Bob123!"' in login
    assert "{% if demo_mode %}" in login


def test_new_user_onboarding_has_next_steps():
    dashboard = _read(TEMPLATES / "dashboard.html")
    assert "{% if is_new_user %}" in dashboard
    for href in ("/courses/new", "/imports", "/imports?mode=paste", "/inbox"):
        assert f'href="{href}"' in dashboard


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
