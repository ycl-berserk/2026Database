from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_conn
from app.errors import ValidationError
from app.main import app
from app.services import (
    ai_service,
    ai_settings_service,
    embedding_service,
    text2sql_service,
    tutor_service,
)


def _actor(base) -> dict:
    return {"id": base["users"]["alice"], "system_role": "student"}


@pytest.fixture
def ai_settings_clean(conn):
    conn.execute("DELETE FROM app.ai_settings")
    yield
    conn.execute("DELETE FROM app.ai_settings")


@pytest.fixture
def ai_modes():
    names = (
        "suggest_mode",
        "tutor_mode",
        "text2sql_mode",
        "embedding_mode",
        "hybrid_search_enabled",
        "session_secret",
    )
    original = {name: getattr(settings, name) for name in names}

    def apply(**values):
        for name, value in values.items():
            object.__setattr__(settings, name, value)

    yield apply
    for name, value in original.items():
        object.__setattr__(settings, name, value)


def _payload(**overrides) -> dict:
    payload = {
        "llm_enabled": True,
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-chat",
        "llm_api_key": "sk-test-123456",
        "clear_llm_key": False,
        "embedding_enabled": True,
        "embedding_base_url": "https://api.deepseek.com/v1",
        "embedding_model": "text-embedding-3-small",
        "embedding_dim": "1536",
        "embedding_api_key": "sk-embed-abcdef",
        "clear_embedding_key": False,
    }
    payload.update(overrides)
    return payload


def test_save_encrypts_and_masks_keys(conn, base, ai_settings_clean):
    result = ai_settings_service.save(conn, _actor(base), _payload())
    assert result["llm_key_masked"] == "••••3456"
    assert result["embedding_key_masked"] == "••••cdef"
    assert result["llm_key_source"] == "database"

    raw = conn.execute(
        "SELECT llm_api_key_enc, embedding_api_key_enc FROM app.ai_settings WHERE id = 1"
    ).fetchone()
    assert b"sk-test-123456" not in bytes(raw["llm_api_key_enc"])
    assert b"sk-embed-abcdef" not in bytes(raw["embedding_api_key_enc"])

    config = ai_settings_service.get_config(conn)
    assert config.llm_api_key == "sk-test-123456"
    assert config.embedding_api_key == "sk-embed-abcdef"
    assert config.llm_base_url == "https://api.deepseek.com/v1"


def test_blank_key_keeps_existing_and_clear_removes(conn, base, ai_settings_clean):
    ai_settings_service.save(conn, _actor(base), _payload())
    ai_settings_service.save(
        conn,
        _actor(base),
        _payload(llm_api_key="", embedding_api_key=""),
    )
    config = ai_settings_service.get_config(conn)
    assert config.llm_api_key == "sk-test-123456"

    ai_settings_service.save(
        conn,
        _actor(base),
        _payload(llm_api_key="", clear_llm_key=True),
    )
    raw = conn.execute(
        "SELECT llm_api_key_enc FROM app.ai_settings WHERE id = 1"
    ).fetchone()
    assert raw["llm_api_key_enc"] is None


def test_invalid_url_is_rejected(conn, base, ai_settings_clean):
    with pytest.raises(ValidationError):
        ai_settings_service.save(
            conn, _actor(base), _payload(llm_base_url="ftp://example.com")
        )


def test_modes_follow_database_settings(conn, base, ai_settings_clean, ai_modes):
    ai_modes(
        suggest_mode="auto",
        tutor_mode="auto",
        text2sql_mode="auto",
        embedding_mode="auto",
        hybrid_search_enabled=True,
    )
    ai_settings_service.save(conn, _actor(base), _payload())
    assert ai_service.mode(conn) == "llm"
    assert tutor_service.mode(conn) == "llm"
    assert text2sql_service.provider_mode(conn) == "llm"
    assert embedding_service.enabled(ai_settings_service.get_config(conn))

    ai_settings_service.save(
        conn,
        _actor(base),
        _payload(llm_enabled=False, embedding_enabled=False),
    )
    assert ai_service.mode(conn) == "off"
    assert tutor_service.mode(conn) == "off"
    assert text2sql_service.provider_mode(conn) == "mock"
    assert not embedding_service.enabled(ai_settings_service.get_config(conn))


def test_secret_rotation_reports_error_instead_of_crashing(
    conn, base, ai_settings_clean, ai_modes
):
    ai_modes(session_secret="first-secret")
    ai_settings_service.save(conn, _actor(base), _payload())
    ai_modes(session_secret="rotated-secret")
    config = ai_settings_service.get_config(conn)
    assert config.secrets_error is True
    assert config.llm_api_key == ""


def test_settings_page_requires_admin_and_never_echoes_key(conn, base, ai_settings_clean):
    app.dependency_overrides[get_conn] = lambda: conn
    try:
        client = TestClient(app)
        html = client.get("/login").text
        token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
        client.post(
            "/login",
            data={"username": "alice", "password": "Passw0rd!", "csrf_token": token},
            follow_redirects=False,
        )
        assert client.get("/settings/ai").status_code == 403

        client.cookies.clear()
        html = client.get("/login").text
        token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
        client.post(
            "/login",
            data={"username": "admin", "password": "Passw0rd!", "csrf_token": token},
            follow_redirects=False,
        )
        page = client.get("/settings/ai")
        assert page.status_code == 200
        assert "AI 设置" in page.text

        token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
        response = client.post(
            "/settings/ai",
            data={
                **_payload(llm_api_key="sk-route-9876"),
                "llm_enabled": "on",
                "embedding_enabled": "on",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get("/settings/ai")
        assert "••••9876" in page.text
        assert "sk-route-9876" not in page.text
    finally:
        app.dependency_overrides.clear()
