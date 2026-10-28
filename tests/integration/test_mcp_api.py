from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth import require_token
from app.db import get_conn
from app.errors import AppError
from app.routers import mcp as mcp_router


def _client(conn, user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(mcp_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[require_token] = lambda: user

    @app.exception_handler(AppError)
    async def handle_app_error(_request, exc: AppError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return TestClient(app)


def _user(base, name: str, scopes=("read", "ingest", "annotate")) -> dict:
    return {
        "id": base["users"][name],
        "display_name": name,
        "system_role": "student",
        "scopes": set(scopes),
    }


def test_me_reports_scopes_and_rejects_admin(conn, base):
    response = _client(conn, _user(base, "alice", ("read", "annotate"))).get("/api/mcp/me")
    assert response.status_code == 200
    assert response.json()["scopes"] == ["annotate", "read"]

    admin = _client(conn, {"id": base["users"]["admin"], "system_role": "admin"})
    assert admin.get("/api/mcp/me").status_code == 403


def test_search_requires_read_scope(conn, base):
    reader = _client(conn, _user(base, "alice", ("read",)))
    response = reader.get("/api/mcp/search", params={"q": "隔离级别", "limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "隔离级别"
    assert any(
        row["id"] == base["conversations"]["db-1"] for row in payload["conversations"]
    )
    assert payload["retrieval"]["mode"] == "lexical"
    assert payload["conversations"][0]["snippet"]

    ingest_only = _client(conn, _user(base, "alice", ("ingest",)))
    denied = ingest_only.get("/api/mcp/search", params={"q": "事务"})
    assert denied.status_code == 403
    assert "读取" in denied.json()["detail"]


def test_conversation_returns_messages_and_annotations(conn, base):
    client = _client(conn, _user(base, "alice", ("read",)))
    response = client.get(f"/api/mcp/conversations/{base['conversations']['db-1']}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation"]["title"] == "事务与隔离级别"
    assert payload["counts"]["total"] == 4
    first = payload["messages"][1]
    assert first["seq_no"] == 2
    assert first["annotations"][0]["content"] == "隔离级别要能对比记忆。"

    bob = _client(conn, _user(base, "bob", ("read",)))
    assert bob.get(f"/api/mcp/conversations/{base['conversations']['misc-1']}").status_code == 404


def test_due_reviews_lists_only_due_items(conn, base):
    client = _client(conn, _user(base, "alice", ("read",)))
    response = client.get("/api/mcp/reviews/due")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["conversation_title"] == "事务与隔离级别"
    assert item["annotation_content"] == "隔离级别要能对比记忆。"
    assert item["is_overdue"] is False
    assert item["due_date"]

    bob = _client(conn, _user(base, "bob", ("read",)))
    assert bob.get("/api/mcp/reviews/due").json()["total"] == 0


def test_add_annotation_requires_scope_and_editable_conversation(conn, base):
    message_id = base["messages"]["db-1"]
    read_only = _client(conn, _user(base, "alice", ("read",)))
    denied = read_only.post(
        "/api/mcp/annotations",
        json={"message_id": message_id, "kind": "note", "content": "只读令牌"},
    )
    assert denied.status_code == 403

    viewer = _client(conn, _user(base, "bob"))
    forbidden = viewer.post(
        "/api/mcp/annotations",
        json={"message_id": message_id, "kind": "note", "content": "viewer 写入"},
    )
    assert forbidden.status_code == 403

    writer = _client(conn, _user(base, "alice"))
    first = writer.post(
        "/api/mcp/annotations",
        json={
            "message_id": message_id,
            "kind": "question",
            "content": "隔离级别和锁的关系？",
            "event_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert first.status_code == 200
    assert first.json()["annotation"]["duplicate"] is False
    again = writer.post(
        "/api/mcp/annotations",
        json={
            "message_id": message_id,
            "kind": "question",
            "content": "隔离级别和锁的关系？",
            "event_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert again.json()["annotation"]["duplicate"] is True

    invalid = writer.post(
        "/api/mcp/annotations",
        json={"message_id": message_id, "kind": "unknown", "content": "x"},
    )
    assert invalid.status_code == 400
