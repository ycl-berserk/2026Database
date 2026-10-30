from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.auth import require_user
from app.db import get_conn
from app.errors import ValidationError
from app.routers import harness as harness_router
from app.services import harness_service, import_service

SESSION_ID = "019dfd6b-ef8f-7c03-b156-eabec74e77d0"


def _write_codex(root, session_id: str = SESSION_ID):
    path = root / "2026" / "05" / "06" / f"rollout-2026-05-06T21-13-22-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "session_meta",
            "timestamp": "2026-05-06T13:13:22Z",
            "payload": {"id": session_id, "cwd": "C:/work/demo"},
        },
        {
            "type": "response_item",
            "timestamp": "2026-05-06T13:13:26Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "MVCC 是怎么实现的？"}],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-05-06T13:13:30Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "通过版本链和快照实现多版本并发控制。"}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    return root


def _client(conn, user: dict) -> TestClient:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-csrf-only")
    app.include_router(harness_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def test_stage_and_confirm_local_session(conn, base, tmp_path, monkeypatch):
    root = _write_codex(tmp_path / "codex")
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(root))
    user = {"id": base["users"]["alice"], "system_role": "student"}

    batch = harness_service.stage(
        conn, user=user, harness_key="codex", session_ids=[SESSION_ID]
    )
    assert batch["preview_token"] is not None

    counts = import_service.confirm_import(
        conn,
        token=str(batch["preview_token"]),
        user=user,
        selected={0},
        course_map={0: base["courses"]["DB"]},
    )
    assert counts["conversations_new"] == 1
    assert counts["messages_new"] == 2

    row = conn.execute(
        "SELECT c.title, c.course_id, s.platform, s.account_label "
        "FROM app.conversations c JOIN app.sources s ON s.id = c.source_id "
        "WHERE c.external_id = %s",
        (f"codex-{SESSION_ID}",),
    ).fetchone()
    assert row["platform"] == "codex"
    assert row["account_label"] == "codex-cli"
    assert row["course_id"] == base["courses"]["DB"]
    assert row["title"] == "MVCC 是怎么实现的？"


def test_reimport_skips_duplicate_messages(conn, base, tmp_path, monkeypatch):
    root = _write_codex(tmp_path / "codex")
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(root))
    user = {"id": base["users"]["alice"], "system_role": "student"}

    first = harness_service.stage(
        conn, user=user, harness_key="codex", session_ids=[SESSION_ID]
    )
    import_service.confirm_import(
        conn,
        token=str(first["preview_token"]),
        user=user,
        selected={0},
        course_map={},
    )
    second = harness_service.stage(
        conn, user=user, harness_key="codex", session_ids=[SESSION_ID]
    )
    counts = import_service.confirm_import(
        conn,
        token=str(second["preview_token"]),
        user=user,
        selected={0},
        course_map={},
    )

    assert counts["conversations_new"] == 0
    assert counts["messages_new"] == 0
    assert counts["messages_skipped"] == 2


def test_stage_rejects_unknown_session(conn, base, tmp_path, monkeypatch):
    root = _write_codex(tmp_path / "codex")
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(root))
    user = {"id": base["users"]["alice"], "system_role": "student"}

    with pytest.raises(ValidationError):
        harness_service.stage(
            conn, user=user, harness_key="codex", session_ids=["../../etc/passwd"]
        )


def test_stage_rejects_unknown_harness(conn, base):
    user = {"id": base["users"]["alice"], "system_role": "student"}

    with pytest.raises(ValidationError):
        harness_service.stage(conn, user=user, harness_key="nope", session_ids=["x"])


def test_local_page_lists_sessions_and_stages(conn, base, tmp_path, monkeypatch):
    root = _write_codex(tmp_path / "codex")
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(root))
    user = {"id": base["users"]["alice"], "system_role": "student"}
    client = _client(conn, user)

    page = client.get("/imports/local")
    assert page.status_code == 200
    assert "MVCC 是怎么实现的？" in page.text

    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token is not None
    response = client.post(
        "/imports/local/stage",
        data={
            "csrf_token": token.group(1),
            "harness": "codex",
            "session": SESSION_ID,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/imports/preview/")
