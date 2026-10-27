from __future__ import annotations

import hashlib

import pytest

from app.errors import AuthRequired, NotFoundError, RateLimited, ValidationError
from app.services import import_service, token_service


def _user_ctx(user_id: int) -> dict:
    return {"id": user_id, "system_role": "student"}


def _document(external_id: str = "api-1", course_code: str | None = "DB") -> dict:
    return {
        "platform": "qoder",
        "account_label": "api-test",
        "conversations": [
            {
                "external_id": external_id,
                "title": "令牌采集的会话",
                "course_code": course_code,
                "started_at": "2026-03-01T09:00:00+08:00",
                "tags": ["令牌"],
                "messages": [
                    {"role": "user", "content": "通过 API 写入的问题"},
                    {"role": "assistant", "content": "通过 API 写入的回答"},
                ],
            }
        ],
    }


def test_token_is_hashed_and_authenticates(conn, base):
    created = token_service.create_token(conn, base["users"]["alice"], "浏览器扩展")
    stored = conn.execute(
        "SELECT token_hash, token_prefix FROM app.api_tokens WHERE id = %s",
        (created["id"],),
    ).fetchone()
    digest = hashlib.sha256(created["token"].encode("utf-8")).digest()
    assert bytes(stored["token_hash"]) == digest
    assert created["token"].startswith(stored["token_prefix"])
    assert created["token"] not in created["token_prefix"]

    info = token_service.authenticate(conn, created["token"])
    assert info["user_id"] == base["users"]["alice"]
    assert info["system_role"] == "student"
    assert info["window_count"] == 1


def test_token_name_is_validated(conn, base):
    with pytest.raises(ValidationError):
        token_service.create_token(conn, base["users"]["alice"], "   ")
    with pytest.raises(ValidationError):
        token_service.create_token(conn, base["users"]["alice"], "x" * 61)


def test_revoke_invalidates_token_and_respects_owner(conn, base):
    created = token_service.create_token(conn, base["users"]["alice"], "临时脚本")
    with pytest.raises(NotFoundError):
        token_service.revoke_token(conn, created["id"], base["users"]["bob"])

    token_service.revoke_token(conn, created["id"], base["users"]["alice"])
    with pytest.raises(AuthRequired):
        token_service.authenticate(conn, created["token"])
    with pytest.raises(NotFoundError):
        token_service.revoke_token(conn, created["id"], base["users"]["alice"])

    tokens = token_service.list_tokens(conn, base["users"]["alice"])
    assert tokens[0]["revoked_at"] is not None


def test_inactive_user_token_is_rejected(conn, base):
    created = token_service.create_token(conn, base["users"]["alice"], "停用账号")
    conn.execute(
        "UPDATE app.users SET is_active = false WHERE id = %s",
        (base["users"]["alice"],),
    )
    with pytest.raises(AuthRequired):
        token_service.authenticate(conn, created["token"])


def test_rate_limit_counts_per_token_window(conn, base):
    created = token_service.create_token(conn, base["users"]["alice"], "高频脚本")
    conn.execute(
        "UPDATE app.api_tokens SET window_started_at = now(), window_count = 60 "
        "WHERE id = %s",
        (created["id"],),
    )
    with pytest.raises(RateLimited):
        token_service.authenticate(conn, created["token"])

    conn.execute(
        "UPDATE app.api_tokens "
        "SET window_started_at = now() - interval '2 minutes' "
        "WHERE id = %s",
        (created["id"],),
    )
    info = token_service.authenticate(conn, created["token"])
    assert info["window_count"] == 1


def test_ingest_json_maps_course_and_is_idempotent(conn, base):
    user = _user_ctx(base["users"]["alice"])
    first = import_service.ingest_json(conn, user=user, document=_document())
    assert first["conversations_new"] == 1
    assert first["messages_new"] == 2
    assert first["conversations"][0]["course_id"] == base["courses"]["DB"]

    again = import_service.ingest_json(conn, user=user, document=_document())
    assert again["conversations_new"] == 0
    assert again["conversations_updated"] == 1
    assert again["messages_new"] == 0
    assert again["messages_skipped"] == 2

    count = conn.execute(
        "SELECT count(*) AS n FROM app.messages WHERE content = '通过 API 写入的问题'"
    ).fetchone()["n"]
    assert count == 1
    tag = conn.execute(
        "SELECT count(*) AS n FROM app.conversation_tags ct "
        "JOIN app.tags t ON t.id = ct.tag_id WHERE t.name = '令牌'"
    ).fetchone()["n"]
    assert tag == 1


def test_ingest_json_rejects_invalid_payload_and_foreign_course(conn, base):
    alice = _user_ctx(base["users"]["alice"])
    with pytest.raises(ValidationError):
        import_service.ingest_json(conn, user=alice, document=["not", "an", "object"])
    with pytest.raises(ValidationError):
        import_service.ingest_json(
            conn,
            user=alice,
            document={"platform": "unknown", "conversations": []},
        )
    with pytest.raises(ValidationError):
        import_service.ingest_json(
            conn,
            user=_user_ctx(base["users"]["bob"]),
            document=_document(),
            course_id=base["courses"]["DB"],
        )
def test_ingest_json_without_known_course_code_lands_in_inbox(conn, base):
    user = _user_ctx(base["users"]["alice"])
    result = import_service.ingest_json(
        conn, user=user, document=_document(external_id="api-inbox", course_code="NOPE")
    )
    assert result["conversations_new"] == 1
    assert result["conversations"][0]["course_id"] is None
