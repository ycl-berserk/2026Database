from __future__ import annotations

import json
import re
from pathlib import Path

EXTENSION = Path(__file__).resolve().parents[2] / "extension"


def _read(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


def test_manifest_declares_mv3_minimal_permissions_and_supported_hosts():
    manifest = json.loads(_read("manifest.json"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"activeTab", "storage"}
    assert "host_permissions" not in manifest
    assert set(manifest["optional_host_permissions"]) == {
        "http://*/*",
        "https://*/*",
    }
    assert manifest["action"]["default_popup"] == "popup.html"

    scripts = manifest["content_scripts"]
    assert len(scripts) == 1
    assert scripts[0]["js"] == ["content.js"]
    assert scripts[0]["run_at"] == "document_idle"
    matches = set(scripts[0]["matches"])
    for host in (
        "https://chatgpt.com/*",
        "https://chat.deepseek.com/*",
        "https://*.qoder.com/*",
    ):
        assert host in matches


def test_popup_sends_only_after_explicit_click_with_bearer_token():
    page = _read("popup.html")
    script = _read("popup.js")

    assert 'id="send"' in page and "disabled" in page
    assert "当前会话只在你点击后上传" in page
    assert "只保存在本机浏览器" in page
    assert "/tokens" in page
    assert "导出" in page

    assert "chrome.storage.local" in script
    assert "Authorization: `Bearer ${settings.token}`" in script
    assert "/api/ingest" in script
    assert "$(\"send\").addEventListener(\"click\", sendCurrent)" in script
    assert "function ensureHostPermission" in script
    assert "chrome.permissions.request" in script
    assert "chrome.permissions.contains" not in script
    assert "const document =" not in script
    assert not re.search(r"ls_[A-Za-z0-9_-]{20,}", _read("popup.html") + script)


def test_content_script_extracts_on_request_without_uploading():
    script = _read("content.js")

    assert "LS_EXTRACT" in script
    assert "chrome.runtime.onMessage.addListener" in script
    assert "globalThis.__lsExtract" in script
    assert "MAX_MESSAGES = 2000" in script
    assert "chatgpt" in script and "deepseek" in script and "qoder" in script
    assert "data-message-author-role" in script
    assert "current_node" not in script
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
    assert "chrome.storage" not in script


def test_extension_readme_documents_privacy_permissions_and_fallback():
    readme = _read("README.md")

    assert "加载已解压的扩展程序" in readme
    assert "仅点击发送" in readme
    assert "chrome.storage.local" in readme
    assert "activeTab" in readme and "storage" in readme
    assert "导出对话" in readme
    assert "模拟" in readme
    assert "令牌" in readme and "/tokens" in readme


def test_popup_styles_follow_site_accessibility_basics():
    css = _read("popup.css")

    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "width: 360px" in css
    assert "color-scheme" not in css or "light" in css
