from __future__ import annotations

import json

import pytest

from app.errors import ConflictError, NotFoundError, ValidationError
from app.services import import_service


def _raw(
    *,
    external_id: str = "imp-1",
    title: str = "MVCC 与事务隔离",
    course_code: str | None = "DB",
    first: str = "MVCC 是怎么实现的？",
    second: str = "通过版本链和快照实现多版本并发控制。",
) -> bytes:
    conversation = {
        "external_id": external_id,
        "title": title,
        "started_at": "2026-03-01T09:00:00+08:00",
        "tags": ["MVCC", "事务"],
        "messages": [
            {
                "role": "user",
                "content": first,
                "created_at": "2026-03-01T09:00:00+08:00",
            },
            {
                "role": "assistant",
                "content": second,
                "created_at": "2026-03-01T09:00:30+08:00",
            },
        ],
    }
    if course_code:
        conversation["course_code"] = course_code
    return json.dumps(
        {
            "platform": "deepseek",
            "account_label": "alice@deepseek",
            "exported_at": "2026-03-02T00:00:00+08:00",
            "conversations": [conversation],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _prepare(conn, base, raw: bytes):
    result = import_service.parse_upload(
        "sample.json", raw, platform=None, account_label=None
    )
    assert result.ok, result.errors
    source_id = import_service.get_or_create_source(
        conn, base["users"]["alice"], result.platform, result.account_label
    )
    saved = import_service.save_preview(
        conn,
        user_id=base["users"]["alice"],
        source_id=source_id,
        file_name="sample.json",
        raw=raw,
        result=result,
    )
    batch = import_service.load_batch(conn, str(saved["preview_token"]), base["users"]["alice"])
    return result, batch


def _confirm(conn, base, batch, *, selected=(0,), course_map=None):
    return import_service.confirm_import(
        conn,
        token=str(batch["preview_token"]),
        user={"id": base["users"]["alice"], "system_role": "student"},
        selected=set(selected),
        course_map=course_map or {},
    )


def test_preview_classifies_new_and_suggests_course(conn, base):
    _, batch = _prepare(conn, base, _raw())
    context = import_service.preview_context(conn, batch, base["users"]["alice"])

    assert context["counts"] == {"new": 1, "update": 0, "duplicate": 0}
    row = context["rows"][0]
    assert row["status"] == "new"
    assert row["new_messages"] == 2
    assert row["skipped_messages"] == 0
    assert row["conflict_messages"] == 0
    assert row["suggested_course_id"] == base["courses"]["DB"]
    assert {course["code"] for course in context["courses"]} == {"DB", "OS"}
    assert context["expired"] is False


def test_confirm_import_counts_and_reimport_is_idempotent(conn, base):
    raw = _raw()
    _, batch = _prepare(conn, base, raw)
    result = _confirm(conn, base, batch, course_map={0: base["courses"]["DB"]})

    assert result["conversations_new"] == 1
    assert result["conversations_updated"] == 0
    assert result["messages_new"] == 2
    assert result["messages_skipped"] == 0
    assert result["conflicts"] == 0
    assert result["tags_new"] == 1  # 事务 已在 base fixture 中创建
    assert result["tags_linked"] == 2

    conversation = conn.execute(
        "SELECT id, course_id FROM app.conversations WHERE external_id = 'imp-1'"
    ).fetchone()
    assert conversation["course_id"] == base["courses"]["DB"]
    messages = conn.execute(
        "SELECT count(*) AS n FROM app.messages WHERE conversation_id = %s",
        (conversation["id"],),
    ).fetchone()
    assert messages["n"] == 2

    with pytest.raises(ConflictError):
        _confirm(conn, base, batch)

    _, second = _prepare(conn, base, raw)
    context = import_service.preview_context(conn, second, base["users"]["alice"])
    assert context["counts"]["duplicate"] == 1
    assert context["rows"][0]["status"] == "duplicate"

    again = _confirm(conn, base, second)
    assert again["conversations_new"] == 0
    assert again["conversations_updated"] == 1
    assert again["messages_new"] == 0
    assert again["messages_skipped"] == 2
    assert again["conflicts"] == 0


def test_content_conflict_is_detected_not_overwritten(conn, base):
    _, batch = _prepare(conn, base, _raw())
    _confirm(conn, base, batch, course_map={0: base["courses"]["DB"]})

    _, second = _prepare(conn, base, _raw(first="MVCC 的实现细节是什么？"))
    context = import_service.preview_context(conn, second, base["users"]["alice"])
    assert context["rows"][0]["status"] == "update"
    assert context["rows"][0]["conflict_messages"] == 1
    assert context["rows"][0]["skipped_messages"] == 1

    result = _confirm(conn, base, second)
    assert result["messages_new"] == 0
    assert result["messages_skipped"] == 1
    assert result["conflicts"] == 1

    stored = conn.execute(
        "SELECT content FROM app.messages WHERE seq_no = 1 AND conversation_id = "
        "(SELECT id FROM app.conversations WHERE external_id = 'imp-1')"
    ).fetchone()
    assert stored["content"] == "MVCC 是怎么实现的？"


def test_confirm_rejects_course_without_edit_permission(conn, base):
    with conn.transaction():
        course = conn.execute(
            "INSERT INTO app.courses (code, name, semester, created_by) "
            "VALUES ('ALG', '算法', '2026 秋', %s) RETURNING id",
            (base["users"]["bob"],),
        ).fetchone()
        conn.execute(
            "INSERT INTO app.course_members (course_id, user_id, role) "
            "VALUES (%s, %s, 'owner'), (%s, %s, 'viewer')",
            (course["id"], base["users"]["bob"], course["id"], base["users"]["alice"]),
        )

    _, batch = _prepare(conn, base, _raw())
    with pytest.raises(ValidationError):
        _confirm(conn, base, batch, course_map={0: course["id"]})

    status = conn.execute(
        "SELECT status FROM app.import_batches WHERE preview_token = %s",
        (batch["preview_token"],),
    ).fetchone()
    assert status["status"] == "preview"


def test_expired_preview_is_rejected_once(conn, base):
    _, batch = _prepare(conn, base, _raw())
    conn.execute(
        "UPDATE app.import_batches "
        "SET created_at = now() - interval '1 hour', expires_at = now() - interval '1 minute' "
        "WHERE id = %s",
        (batch["id"],),
    )
    with pytest.raises(ValidationError):
        _confirm(conn, base, batch)

    status = conn.execute(
        "SELECT status FROM app.import_batches WHERE id = %s", (batch["id"],)
    ).fetchone()
    assert status["status"] == "expired"
    with pytest.raises(ConflictError):
        _confirm(conn, base, batch)


def test_preview_token_is_scoped_to_owner(conn, base):
    _, batch = _prepare(conn, base, _raw())
    with pytest.raises(NotFoundError):
        import_service.load_batch(conn, str(batch["preview_token"]), base["users"]["bob"])
    with pytest.raises(NotFoundError):
        import_service.load_batch(conn, "not-a-uuid", base["users"]["alice"])


def test_broken_files_are_rejected(conn, base):
    broken = import_service.parse_upload(
        "broken.json",
        b'{"platform": "qoder", "conversations": [{"title": "no id"}]}',
        platform=None,
        account_label=None,
    )
    assert not broken.ok
    assert any("external_id" in error for error in broken.errors)

    unknown = import_service.parse_upload(
        "notes.pdf", b"%PDF", platform=None, account_label=None
    )
    assert not unknown.ok
    assert "仅支持" in unknown.errors[0]


def test_source_get_or_create_is_idempotent(conn, base):
    first = import_service.get_or_create_source(
        conn, base["users"]["alice"], "deepseek", "alice@deepseek"
    )
    second = import_service.get_or_create_source(
        conn, base["users"]["alice"], "deepseek", "alice@deepseek"
    )
    assert first == second
