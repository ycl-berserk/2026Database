from __future__ import annotations

import json
from typing import Any

from . import adapters
from .base import (
    COURSE_CODE_RE,
    PLATFORMS,
    ImportResult,
    check_limits,
    clean_content,
    map_sender,
    normalize_tags,
    parse_timestamp,
)


def parse(
    raw: bytes,
    *,
    platform_override: str | None = None,
    account_override: str | None = None,
    max_messages: int = 2000,
) -> ImportResult:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ImportResult("", "", None, errors=["文件不是有效的 UTF-8 文本"])

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return ImportResult(
            "", "", None,
            errors=[f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"],
        )

    doc, adapter_warnings = adapters.normalize_json(doc)
    warnings.extend(adapter_warnings)

    if isinstance(doc, list):
        conversations_raw = doc
        doc = {}
        warnings.append("根节点是数组，已按会话列表处理")
    elif isinstance(doc, dict):
        conversations_raw = doc.get("conversations")
    else:
        return ImportResult("", "", None, errors=["JSON 根节点必须是对象或数组"])

    platform = str(platform_override or doc.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        errors.append(f"平台必须是 {', '.join(PLATFORMS)} 之一")
    account_label = str(
        account_override or doc.get("account_label") or doc.get("account") or "default"
    ).strip()[:100] or "default"
    exported_at = parse_timestamp(doc.get("exported_at"), "导出时间", errors, "文件头")

    if not isinstance(conversations_raw, list) or not conversations_raw:
        errors.append("缺少 conversations 数组，或数组为空")
        return ImportResult(platform, account_label, exported_at, errors=errors, warnings=warnings)
    if len(conversations_raw) > 200:
        errors.append(f"单次导入会话数 {len(conversations_raw)} 超过上限 200")
        return ImportResult(platform, account_label, exported_at, errors=errors, warnings=warnings)

    conversations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, raw_conv in enumerate(conversations_raw, start=1):
        where = f"第 {index} 个会话"
        if not isinstance(raw_conv, dict):
            errors.append(f"{where}：必须是对象")
            continue

        external_id = str(raw_conv.get("external_id") or raw_conv.get("id") or "").strip()
        if not external_id:
            errors.append(f"{where}：缺少 external_id")
        elif len(external_id) > 200:
            errors.append(f"{where}：external_id 超过 200 字符")
        elif external_id in seen_ids:
            errors.append(f"{where}：external_id「{external_id}」在文件内重复")
        else:
            seen_ids.add(external_id)

        title = str(raw_conv.get("title") or "").strip()
        if not title:
            errors.append(f"{where}：缺少会话标题")
        elif len(title) > 255:
            errors.append(f"{where}：标题超过 255 字符")

        course_code = str(raw_conv.get("course_code") or "").strip() or None
        if course_code and not COURSE_CODE_RE.match(course_code):
            warnings.append(f"{where}：课程编号「{course_code}」格式不合法，已忽略")
            course_code = None

        started_at = parse_timestamp(raw_conv.get("started_at"), "开始时间", errors, where)
        ended_at = parse_timestamp(raw_conv.get("ended_at"), "结束时间", errors, where)
        if started_at and ended_at and ended_at < started_at:
            errors.append(f"{where}：结束时间早于开始时间")

        tags = normalize_tags(raw_conv.get("tags"))
        messages_raw = raw_conv.get("messages")
        if not isinstance(messages_raw, list) or not messages_raw:
            errors.append(f"{where}：缺少非空的 messages 数组")
            continue

        messages: list[dict[str, Any]] = []
        for msg_index, raw_msg in enumerate(messages_raw, start=1):
            msg_where = f"{where} 第 {msg_index} 条消息"
            if not isinstance(raw_msg, dict):
                errors.append(f"{msg_where}：必须是对象")
                continue
            sender = map_sender(
                raw_msg.get("role") or raw_msg.get("sender"), errors, msg_where
            )
            content = clean_content(raw_msg.get("content") or raw_msg.get("text"))
            if not content:
                errors.append(f"{msg_where}：内容为空")
                continue
            created_at = parse_timestamp(
                raw_msg.get("created_at") or raw_msg.get("time"),
                "消息时间",
                errors,
                msg_where,
            )
            messages.append(
                {
                    "seq_no": len(messages) + 1,
                    "sender": sender,
                    "content": content,
                    "created_at": created_at or started_at,
                }
            )

        if not messages:
            errors.append(f"{where}：没有有效的消息")
            continue

        conversations.append(
            {
                "external_id": external_id,
                "title": title,
                "course_code": course_code,
                "started_at": started_at,
                "ended_at": ended_at,
                "tags": tags,
                "messages": messages,
            }
        )

    check_limits(conversations, errors, max_messages=max_messages)
    return ImportResult(
        platform,
        account_label,
        exported_at,
        conversations=conversations,
        errors=errors,
        warnings=warnings,
    )
