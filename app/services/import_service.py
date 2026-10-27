from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from ..config import settings
from ..errors import ConflictError, NotFoundError, ValidationError
from ..importers import json_importer, markdown_importer, plain_text_importer
from ..importers.base import PLATFORMS, ImportResult

TAG_COLORS = ["#2f5d4e", "#a8432c", "#a6771f", "#4a6fa5", "#7a5b8f", "#5b7d3a", "#3d7a7a"]


def parse_upload(
    file_name: str,
    raw: bytes,
    *,
    platform: str | None,
    account_label: str | None,
) -> ImportResult:
    suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    account = (account_label or "").strip() or None
    if suffix == "json":
        return json_importer.parse(
            raw, platform_override=platform, account_override=account,
            max_messages=settings.import_max_messages,
        )
    if suffix in ("md", "markdown", "txt"):
        return markdown_importer.parse(
            raw, platform_override=platform, account_override=account,
            max_messages=settings.import_max_messages,
        )
    return ImportResult(
        platform, account or "default", None,
        errors=["仅支持 .json、.md 或 .txt 文件"],
    )


def parse_paste(
    text: str,
    *,
    title: str | None = None,
    platform: str | None = None,
    account_label: str | None = None,
) -> ImportResult:
    normalized = (platform or "").strip().lower()
    if normalized in ("", "auto"):
        normalized = None
    return plain_text_importer.parse(
        text.encode("utf-8"),
        title_override=(title or "").strip() or None,
        platform_override=normalized,
        account_override=(account_label or "").strip() or "paste",
        max_messages=settings.import_max_messages,
    )


def stage_paste(
    conn,
    *,
    user_id: int,
    text: str,
    title: str | None = None,
    platform: str | None = None,
    account_label: str | None = None,
    file_name: str = "粘贴内容",
) -> tuple[ImportResult, dict | None]:
    result = parse_paste(
        text, title=title, platform=platform, account_label=account_label
    )
    if not result.ok or result.platform not in PLATFORMS:
        return result, None
    with conn.transaction():
        source_id = get_or_create_source(
            conn, user_id, result.platform, result.account_label
        )
        batch = save_preview(
            conn,
            user_id=user_id,
            source_id=source_id,
            file_name=file_name,
            raw=text.encode("utf-8"),
            result=result,
        )
    return result, batch


def get_or_create_source(conn, user_id: int, platform: str, account_label: str) -> int:
    row = conn.execute(
        """
        INSERT INTO app.sources (owner_id, platform, account_label)
        VALUES (%s, %s, %s)
        ON CONFLICT (owner_id, platform, account_label)
        DO UPDATE SET account_label = EXCLUDED.account_label
        RETURNING id
        """,
        (user_id, platform, account_label),
    ).fetchone()
    return row["id"]


def save_preview(
    conn,
    *,
    user_id: int,
    source_id: int,
    file_name: str,
    raw: bytes,
    result: ImportResult,
) -> dict:
    token = uuid.uuid4()
    payload = result.payload({"warnings": result.warnings})
    summary = result.summary()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.preview_ttl_minutes)
    row = conn.execute(
        """
        INSERT INTO app.import_batches
            (user_id, source_id, file_name, file_sha256, file_size, status,
             preview_token, payload, summary, expires_at)
        VALUES (%s, %s, %s, %s, %s, 'preview', %s, %s, %s, %s)
        RETURNING id, preview_token, expires_at
        """,
        (
            user_id,
            source_id,
            file_name[:255],
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            token,
            Jsonb(payload),
            Jsonb(summary),
            expires_at,
        ),
    ).fetchone()
    return row


def list_batches(conn, user_id: int) -> list[dict]:
    conn.execute(
        "UPDATE app.import_batches SET status = 'expired' "
        "WHERE user_id = %s AND status = 'preview' AND expires_at <= now()",
        (user_id,),
    )
    return conn.execute(
        """
        SELECT b.id, b.file_name, b.status, b.created_at, b.imported_at, b.expires_at,
               b.preview_token, b.summary, b.file_size, s.platform, s.account_label
          FROM app.import_batches b
          JOIN app.sources s ON s.id = b.source_id
         WHERE b.user_id = %s
         ORDER BY b.created_at DESC, b.id DESC
         LIMIT 30
        """,
        (user_id,),
    ).fetchall()


def load_batch(conn, token: str, user_id: int) -> dict:
    try:
        uuid.UUID(token)
    except (ValueError, AttributeError):
        raise NotFoundError("预览不存在或已失效")
    row = conn.execute(
        """
        SELECT b.*, s.platform, s.account_label
          FROM app.import_batches b
          JOIN app.sources s ON s.id = b.source_id
         WHERE b.preview_token = %s AND b.user_id = %s
        """,
        (token, user_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("预览不存在或已失效")
    return row


def get_batch(conn, batch_id: int, user_id: int) -> dict:
    row = conn.execute(
        """
        SELECT b.*, s.platform, s.account_label
          FROM app.import_batches b
          JOIN app.sources s ON s.id = b.source_id
         WHERE b.id = %s AND b.user_id = %s
        """,
        (batch_id, user_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("导入记录不存在")
    return row


def editable_courses(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT c.id, c.code, c.name, cm.role
          FROM app.courses c
          JOIN app.course_members cm ON cm.course_id = c.id
         WHERE cm.user_id = %s AND cm.role IN ('owner', 'editor')
         ORDER BY c.code
        """,
        (user_id,),
    ).fetchall()


def _existing_state(conn, source_id: int) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT c.external_id, c.id AS conversation_id, c.course_id,
               m.seq_no, md5(m.content) AS content_hash
          FROM app.conversations c
          LEFT JOIN app.messages m ON m.conversation_id = c.id
         WHERE c.source_id = %s
        """,
        (source_id,),
    ).fetchall()
    state: dict[str, dict] = {}
    for row in rows:
        entry = state.setdefault(
            row["external_id"],
            {
                "conversation_id": row["conversation_id"],
                "course_id": row["course_id"],
                "messages": {},
            },
        )
        if row["seq_no"] is not None:
            entry["messages"][row["seq_no"]] = row["content_hash"]
    return state


def preview_context(conn, batch: dict, user_id: int) -> dict:
    payload = batch["payload"] or {}
    conversations = payload.get("conversations") or []
    courses = editable_courses(conn, user_id)
    course_by_code = {c["code"].upper(): c["id"] for c in courses}
    course_ids = {c["id"] for c in courses}
    existing = _existing_state(conn, batch["source_id"])

    rows: list[dict] = []
    for index, conv in enumerate(conversations):
        entry = existing.get(conv["external_id"])
        existing_messages = entry["messages"] if entry else {}
        new_count = 0
        skipped = 0
        conflicts = 0
        for msg in conv["messages"]:
            seq = msg["seq_no"]
            digest = hashlib.md5(msg["content"].encode("utf-8")).hexdigest()
            if seq not in existing_messages:
                new_count += 1
            elif existing_messages[seq] == digest:
                skipped += 1
            else:
                conflicts += 1

        if entry is None:
            status = "new"
        elif new_count == 0 and conflicts == 0:
            status = "duplicate"
        else:
            status = "update"

        suggested = course_by_code.get((conv.get("course_code") or "").upper())
        if suggested is None and entry and entry.get("course_id") in course_ids:
            suggested = entry["course_id"]

        rows.append(
            {
                **conv,
                "index": index,
                "status": status,
                "existing_id": entry["conversation_id"] if entry else None,
                "new_messages": new_count,
                "skipped_messages": skipped,
                "conflict_messages": conflicts,
                "suggested_course_id": suggested,
            }
        )

    counts = {
        "new": sum(1 for r in rows if r["status"] == "new"),
        "update": sum(1 for r in rows if r["status"] == "update"),
        "duplicate": sum(1 for r in rows if r["status"] == "duplicate"),
    }
    warnings = payload.get("warnings") or []
    return {
        "batch": batch,
        "rows": rows,
        "courses": courses,
        "counts": counts,
        "warnings": warnings,
        "expired": batch["expires_at"] <= datetime.now(timezone.utc),
        "summary": batch["summary"] or {},
    }


def _get_or_create_tag(conn, user_id: int, name: str) -> tuple[int, bool]:
    color = TAG_COLORS[sum(name.encode("utf-8")) % len(TAG_COLORS)]
    row = conn.execute(
        """
        INSERT INTO app.tags (owner_id, name, kind, color)
        VALUES (%s, %s, 'topic', %s)
        ON CONFLICT (owner_id, name)
        DO UPDATE SET name = EXCLUDED.name
        RETURNING id, (xmax = 0) AS is_new
        """,
        (user_id, name[:40], color),
    ).fetchone()
    return row["id"], bool(row["is_new"])


def ingest_plain_text(
    conn,
    *,
    user: dict,
    text: str,
    title: str | None = None,
    platform: str | None = None,
    account_label: str = "share",
    course_id: int | None = None,
    file_name: str = "分享内容",
) -> dict:
    result = parse_paste(
        text, title=title, platform=platform, account_label=account_label
    )
    if not result.ok:
        raise ValidationError("；".join(result.errors[:3]) or "没有可保存的内容")
    if result.platform not in PLATFORMS:
        raise ValidationError("无法确定来源平台")

    target_course = None
    if course_id is not None:
        allowed = {row["id"] for row in editable_courses(conn, user["id"])}
        if course_id not in allowed:
            raise ValidationError("只能把内容归类到自己可编辑的课程")
        target_course = course_id

    with conn.transaction():
        source_id = get_or_create_source(
            conn, user["id"], result.platform, result.account_label
        )
        batch = save_preview(
            conn,
            user_id=user["id"],
            source_id=source_id,
            file_name=file_name,
            raw=text.encode("utf-8"),
            result=result,
        )
        counts = confirm_import(
            conn,
            token=str(batch["preview_token"]),
            user=user,
            selected={0},
            course_map={0: target_course},
        )

    conversation = conn.execute(
        "SELECT id FROM app.conversations WHERE source_id = %s AND external_id = %s",
        (source_id, result.conversations[0]["external_id"]),
    ).fetchone()
    return {
        **counts,
        "conversation_id": conversation["id"],
        "warnings": result.warnings,
    }


def confirm_import(
    conn,
    *,
    token: str,
    user: dict,
    selected: set[int],
    course_map: dict[int, int | None],
) -> dict:
    batch = load_batch(conn, token, user["id"])
    if batch["status"] != "preview":
        raise ConflictError("该预览已处理，不能重复导入")
    if batch["expires_at"] <= datetime.now(timezone.utc):
        with conn.transaction():
            conn.execute(
                "UPDATE app.import_batches SET status = 'expired' WHERE id = %s",
                (batch["id"],),
            )
        raise ValidationError("预览已过期，请重新上传文件")

    payload = batch["payload"] or {}
    conversations = payload.get("conversations") or []
    if not selected:
        raise ValidationError("请至少选择一个会话")
    if any(index < 0 or index >= len(conversations) for index in selected):
        raise ValidationError("选择项无效，请刷新预览页重试")

    allowed_courses = {c["id"] for c in editable_courses(conn, user["id"])}
    normalized_courses: dict[int, int | None] = {}
    for index in selected:
        course_id = course_map.get(index)
        if course_id is not None and course_id not in allowed_courses:
            raise ValidationError("只能将会话归类到你拥有编辑权限的课程")
        normalized_courses[index] = course_id

    counts = {
        "selected": len(selected),
        "conversations_new": 0,
        "conversations_updated": 0,
        "messages_new": 0,
        "messages_skipped": 0,
        "conflicts": 0,
        "tags_new": 0,
        "tags_linked": 0,
    }

    try:
        with conn.transaction():
            locked = conn.execute(
                "SELECT status, expires_at FROM app.import_batches WHERE id = %s FOR UPDATE",
                (batch["id"],),
            ).fetchone()
            if locked["status"] != "preview":
                raise ConflictError("该预览已被其他请求处理")
            if locked["expires_at"] <= datetime.now(timezone.utc):
                raise ValidationError("预览已过期，请重新上传文件")

            conn.execute("SELECT pg_advisory_xact_lock(%s)", (batch["source_id"],))

            for index in sorted(selected):
                conv = conversations[index]
                course_id = normalized_courses[index]
                existing = conn.execute(
                    "SELECT id, course_id FROM app.conversations "
                    "WHERE source_id = %s AND external_id = %s",
                    (batch["source_id"], conv["external_id"]),
                ).fetchone()

                if existing is None:
                    conversation_id = conn.execute(
                        """
                        INSERT INTO app.conversations
                            (source_id, course_id, external_id, title, started_at, ended_at)
                        VALUES (%s, %s, %s, %s, %s::timestamptz, %s::timestamptz)
                        RETURNING id
                        """,
                        (
                            batch["source_id"],
                            course_id,
                            conv["external_id"],
                            conv["title"],
                            conv.get("started_at"),
                            conv.get("ended_at"),
                        ),
                    ).fetchone()["id"]
                    counts["conversations_new"] += 1
                    existing_messages: dict[int, str] = {}
                else:
                    conversation_id = existing["id"]
                    conn.execute(
                        """
                        UPDATE app.conversations
                           SET title = %s,
                               course_id = COALESCE(%s, course_id),
                               started_at = COALESCE(%s::timestamptz, started_at),
                               ended_at = COALESCE(%s::timestamptz, ended_at)
                         WHERE id = %s
                        """,
                        (
                            conv["title"],
                            course_id,
                            conv.get("started_at"),
                            conv.get("ended_at"),
                            conversation_id,
                        ),
                    )
                    counts["conversations_updated"] += 1
                    existing_messages = {
                        row["seq_no"]: row["content_hash"]
                        for row in conn.execute(
                            "SELECT seq_no, md5(content) AS content_hash "
                            "FROM app.messages WHERE conversation_id = %s",
                            (conversation_id,),
                        ).fetchall()
                    }

                for msg in conv["messages"]:
                    seq = msg["seq_no"]
                    digest = hashlib.md5(msg["content"].encode("utf-8")).hexdigest()
                    if seq in existing_messages:
                        if existing_messages[seq] == digest:
                            counts["messages_skipped"] += 1
                        else:
                            counts["conflicts"] += 1
                        continue
                    inserted = conn.execute(
                        """
                        INSERT INTO app.messages
                            (conversation_id, seq_no, sender, content, created_at)
                        VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
                        ON CONFLICT (conversation_id, seq_no) DO NOTHING
                        RETURNING id
                        """,
                        (
                            conversation_id,
                            seq,
                            msg["sender"],
                            msg["content"],
                            msg.get("created_at"),
                        ),
                    ).fetchone()
                    if inserted is not None:
                        counts["messages_new"] += 1

                for tag_name in conv.get("tags") or []:
                    tag_id, created = _get_or_create_tag(conn, user["id"], tag_name)
                    if created:
                        counts["tags_new"] += 1
                    linked = conn.execute(
                        """
                        INSERT INTO app.conversation_tags
                            (conversation_id, tag_id, created_by)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (conversation_id, tag_id) DO NOTHING
                        """,
                        (conversation_id, tag_id, user["id"]),
                    )
                    counts["tags_linked"] += linked.rowcount

            summary = {
                **counts,
                "preview_conversation_count": len(conversations),
                "preview_message_count": payload.get("message_count")
                or sum(len(c["messages"]) for c in conversations),
            }
            conn.execute(
                """
                UPDATE app.import_batches
                   SET status = 'imported', imported_at = now(),
                       summary = coalesce(summary, '{}'::jsonb) || %s,
                       payload = NULL
                 WHERE id = %s
                """,
                (Jsonb(summary), batch["id"]),
            )
    except Exception as exc:
        try:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE app.import_batches
                       SET status = 'failed',
                           summary = coalesce(summary, '{}'::jsonb) || %s
                     WHERE id = %s AND status = 'preview'
                    """,
                    (Jsonb({"error": str(exc)[:300]}), batch["id"]),
                )
        except Exception:
            pass
        raise

    return {"batch_id": batch["id"], **counts}
