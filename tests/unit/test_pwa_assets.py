from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def _get(path: str):
    client = TestClient(app)
    try:
        return client.get(path)
    finally:
        client.close()


def test_manifest_is_valid_and_installable():
    response = _get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")

    manifest = response.json()
    assert manifest["start_url"] == "/dashboard"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    icon_sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= icon_sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    for icon in manifest["icons"]:
        assert (settings.static_dir / icon["src"].removeprefix("/static/")).is_file()


def test_service_worker_served_from_root_with_scope_header():
    response = _get("/sw.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert "javascript" in response.headers["content-type"]
    body = response.text
    assert 'request.mode === "navigate"' in body
    assert 'caches.match("/static/offline.html")' in body


def test_service_worker_does_not_cache_authenticated_html():
    body = (settings.static_dir / "sw.js").read_text(encoding="utf-8")
    static_branch = body.split('if (url.pathname.startsWith("/static/"))', 1)[1]
    # 只有静态资源分支允许写入缓存；页面 HTML 走网络优先 + 离线兜底。
    assert "cache.put" in static_branch
    navigation_branch = body.split('if (request.mode === "navigate")', 1)[1].split(
        'if (url.pathname.startsWith("/static/"))', 1
    )[0]
    assert "cache.put" not in navigation_branch


def test_offline_page_is_self_contained():
    response = _get("/offline")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "离线" in response.text
    assert "<style>" in response.text
    assert 'src="http' not in response.text
    assert 'href="http' not in response.text
