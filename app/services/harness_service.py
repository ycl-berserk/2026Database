from __future__ import annotations

import json
from typing import Any

from ..config import settings
from ..errors import ValidationError
from ..harness import CONNECTORS, HarnessSession
from ..importers.base import ImportResult
from . import import_service

MAX_SESSIONS = 20
TOTAL_MESSAGE_BUDGET = 4000


def list_sources(conn, user_id: int) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for connector in CONNECTORS.values():
        root = connector.root()
        imported = _imported_external_ids(conn, user_id, connector.platform)
        sessions = []
        if root is not None:
            try:
                sessions = connector.list_sessions()
            except OSError:
                sessions = []
        sources.append(
            {
                "key": connector.key,
                "label": connector.label,
                "platform": connector.platform,
                "available": root is not None,
                "location": str(root or ""),
                "sessions": [
                    {
                        "session_id": session.session_id,
                        "title": session.title,
                        "updated_at": session.updated_at,
                        "message_count": session.message_count,
                        "size_bytes": session.size_bytes,
                        "imported": session.external_id in imported,
                    }
                    for session in sessions
                ],
            }
        )
    return sources


def stage(
    conn,
    *,
    user: dict,
    harness_key: str,
    session_ids: list[str],
) -> dict[str, Any]:
    connector = CONNECTORS.get(harness_key)
    if connector is None:
        raise ValidationError("未知的本地工具")
    if connector.root() is None:
        raise ValidationError("没有找到该工具的本地数据目录")

    known = {session.session_id: session for session in connector.list_sessions()}
    chosen: list[HarnessSession] = [
        known[session_id] for session_id in dict.fromkeys(session_ids) if session_id in known
    ]
    if not chosen:
        raise ValidationError("请至少选择一个可用的会话")
    if len(chosen) > MAX_SESSIONS:
        raise ValidationError(f"单次最多导入 {MAX_SESSIONS} 个会话，请分批进行")

    conversations: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    budget = TOTAL_MESSAGE_BUDGET
    for session in chosen:
        if budget <= 0:
            break
        result = connector.load(session, max_messages=min(settings.import_max_messages, budget))
        conversations.extend(result.conversations)
        warnings.extend(result.warnings)
        errors.extend(result.errors)
        budget -= result.message_count
    if not conversations:
        raise ValidationError("；".join(errors[:3]) or "选中的会话没有可导入的对话文本")
    if len(conversations) < len(chosen):
        warnings.append("部分会话没有对话文本，已跳过")
    if budget <= 0:
        warnings.append("已达到单次导入的消息上限，其余会话请分批导入")

    merged = ImportResult(
        connector.platform,
        connector.account_label,
        None,
        conversations=conversations,
        warnings=warnings,
    )
    raw = json.dumps(merged.payload({"warnings": warnings}), ensure_ascii=False).encode("utf-8")
    with conn.transaction():
        source_id = import_service.get_or_create_source(
            conn, user["id"], connector.platform, connector.account_label
        )
        batch = import_service.save_preview(
            conn,
            user_id=user["id"],
            source_id=source_id,
            file_name=f"{connector.label} 本地会话",
            raw=raw,
            result=merged,
        )
    return batch


def _imported_external_ids(conn, user_id: int, platform: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT c.external_id
          FROM app.conversations c
          JOIN app.sources s ON s.id = c.source_id
         WHERE s.owner_id = %s AND s.platform = %s
        """,
        (user_id, platform),
    ).fetchall()
    return {row["external_id"] for row in rows}
