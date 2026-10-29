from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from app.config import settings


def test_healthz_reports_bind_host_without_changing_existing_fields(monkeypatch):
    monkeypatch.setattr(main_module, "check_main_db", lambda: True)
    monkeypatch.setattr(main_module, "check_t2s_db", lambda: True)
    monkeypatch.setattr(main_module, "_initialized", lambda: True)

    client = TestClient(main_module.app)
    response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bind_host"] == settings.bind_host
    for key in ("ok", "app", "db", "t2s_db", "desktop", "initialized"):
        assert key in payload


def test_healthz_reports_503_when_database_check_fails(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(main_module, "check_main_db", boom)
    monkeypatch.setattr(main_module, "check_t2s_db", lambda: True)

    client = TestClient(main_module.app)
    response = client.get("/healthz")

    assert response.status_code == 503
    payload = response.json()
    assert payload["ok"] is False
    assert payload["db"].startswith("error:")
    assert payload["bind_host"] == settings.bind_host
