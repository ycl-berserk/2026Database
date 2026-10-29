from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"
TAURI = ROOT / "desktop" / "src-tauri"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _windows(path: Path) -> dict[str, dict]:
    data = json.loads(_read(path))
    return {window["label"]: window for window in data["app"]["windows"]}


def test_auth_pages_use_compact_panel_without_marketing_hero():
    for name in ("login.html", "setup.html"):
        body = _read(TEMPLATES / name)
        assert "login-intro" not in body
        assert "login-points" not in body
        assert '{% include "partials/desktop_frame.html" %}' in body
        assert 'class="login-card card"' in body

    css = _read(STATIC / "app.css")
    assert ".login-intro" not in css
    assert ".login-points" not in css


def test_desktop_frame_only_draws_titlebar_on_macos_tauri():
    partial = _read(TEMPLATES / "partials" / "desktop_frame.html")
    assert "__TAURI_INTERNALS__" in partial
    assert 'classList.add("is-desktop")' in partial
    assert 'classList.add("is-macos")' in partial
    assert "data-tauri-drag-region" in partial

    css = _read(STATIC / "app.css")
    assert ".desktop-drag { display: none; }" in css
    assert "html.is-desktop.is-macos .desktop-drag" in css
    assert "html.is-desktop.is-macos .app-shell { padding-top: 30px; }" in css
    assert "html.is-desktop.is-macos .sidebar { top: 30px;" in css


def test_macos_overlay_keeps_traffic_lights_in_default_position():
    mac = _windows(TAURI / "tauri.macos.conf.json")
    base = _windows(TAURI / "tauri.conf.json")

    assert set(mac) == set(base)
    for label, window in mac.items():
        for key in ("title", "width", "height", "minWidth", "minHeight"):
            assert window[key] == base[label][key]

    assert mac["main"]["titleBarStyle"] == "Overlay"
    assert mac["main"]["hiddenTitle"] is True
    assert "trafficLightPosition" not in mac["main"]
    assert "titleBarStyle" not in mac["share"]


def test_dashboard_metrics_and_mobile_bar_are_flattened():
    css = _read(STATIC / "app.css")
    assert "background: linear-gradient(180deg, #232b25" not in css
    assert "background: linear-gradient(90deg, var(--seal), var(--amber))" not in css
    assert ".stat-grid {" in css
    assert "border-left: 1px solid var(--line);" in css
    assert ".mobile-tabbar {" in css
    assert "min-height: 48px;" in css
    assert "backdrop-filter: blur(12px);" not in css
