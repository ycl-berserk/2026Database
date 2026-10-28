from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth import require_token
from app.db import get_conn
from app.errors import AppError
from app.routers import api as api_router


def _client(conn, user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(api_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[require_token] = lambda: user

    @app.exception_handler(AppError)
    async def handle_app_error(_request, exc: AppError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return TestClient(app)


def _user(base, name: str) -> dict:
    return {"id": base["users"][name], "system_role": "student"}


def test_api_search_returns_visible_conversations(conn, base):
    client = _client(conn, _user(base, "alice"))
    response = client.get("/api/search", params={"q": "隔离级别"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["q"] == "隔离级别"
    rows = payload["conversations"]["rows"]
    assert any(row["id"] == base["conversations"]["db-1"] for row in rows)
    assert payload["conversations"]["retrieval"]["mode"] == "lexical"


def test_api_search_does_not_leak_other_users(conn, base):
    client = _client(conn, _user(base, "bob"))
    response = client.get("/api/search", params={"q": "索引"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["conversations"]["total"] == 0
    assert payload["annotations"] == []


def test_api_search_rejects_admin_and_blank_query(conn, base):
    admin = {"id": base["users"]["admin"], "system_role": "admin"}
    assert _client(conn, admin).get("/api/search", params={"q": "事务"}).status_code == 403

    alice = _client(conn, _user(base, "alice"))
    assert alice.get("/api/search", params={"q": "   "}).status_code == 400
