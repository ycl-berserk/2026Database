from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import PermissionDenied
from app.routers.api import _require_student

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_conversation_page_exposes_offline_save_and_global_banner():
    detail = _read(TEMPLATES / "conversations" / "detail.html")
    assert "data-offline-save=" in detail

    base = _read(TEMPLATES / "base.html")
    assert "data-offline-banner" in base
    assert 'href="/offline"' in base

    dashboard = _read(TEMPLATES / "dashboard.html")
    assert "data-offline-count-link" in dashboard


def test_offline_reader_is_self_contained_and_manages_storage():
    body = _read(STATIC / "offline.html")
    assert "indexedDB.open" in body
    assert '"conversations"' in body
    assert "清除本机离线数据" in body
    assert "已保存的会话" in body
    assert "renderReader" in body
    assert 'src="http' not in body
    assert 'href="http' not in body


def test_app_script_has_offline_store_and_connection_banner():
    script = _read(STATIC / "app.js")
    assert 'OFFLINE_DB = "ls-offline"' in script
    assert "saveConversation" in script
    assert "data-offline-banner" in script
    assert "navigator.onLine" in script
    assert "updateOfflineCount" in script


def test_service_worker_cache_version_bumped():
    body = _read(STATIC / "sw.js")
    assert 'CACHE_NAME = "ls-shell-v4"' in body


def test_offline_reader_denies_admin_message_bodies():
    _require_student({"system_role": "student"})
    with pytest.raises(PermissionDenied):
        _require_student({"system_role": "admin"})
