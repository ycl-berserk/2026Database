from __future__ import annotations

import re
from typing import Any

from . import adapters
from .base import (
    COURSE_CODE_RE,
    PLATFORMS,
    ImportResult,
    check_limits,
    clean_content,
    external_id_from,
    map_sender,
    normalize_tags,
    parse_timestamp,
)

CONV_HEADING = re.compile(r"^#\s+(.+?)\s*$")
MSG_HEADING = re.compile(r"^##\s+(.+?)(?:\s*\[(.+?)\])?\s*$")
META_LINE = re.compile(r"^\s*[-*]?\s*([\w\u4e00-\u9fa5]+)\s*[:：]\s*(.+?)\s*$")
META_KEYS = {
    "课程": "course_code",
    "标签": "tags",
    "开始": "started_at",
    "开始时间": "started_at",
    "结束": "ended_at",
    "结束时间": "ended_at",
    "会话id": "external_id",
    "会话ID": "external_id",
    "平台": "platform",
    "账号": "account_label",
}


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

    text, adapter_warnings = adapters.normalize_markdown(text)
    warnings.extend(adapter_warnings)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    file_meta: dict[str, str] = {}
    blocks: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = CONV_HEADING.match(line)
        if match:
            if current_title is not None:
                blocks.append((current_title, current_lines))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        if current_title is None:
            meta = META_LINE.match(line)
            if meta and meta.group(1) in META_KEYS:
                file_meta[META_KEYS[meta.group(1)]] = meta.group(2).strip()
            continue
        current_lines.append(line)
    if current_title is not None:
        blocks.append((current_title, current_lines))

    if not blocks:
        return ImportResult(
            "", "", None,
            errors=["Markdown 中未找到一级标题（# 会话标题）"],
            warnings=warnings,
        )

    platform = (platform_override or file_meta.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        errors.append("无法确定来源平台：请在导入表单中选择平台，或在文件头写「平台: qoder」")
    account_label = (
        account_override or file_meta.get("account_label") or "default"
    ).strip()[:100] or "default"

    if len(blocks) > 200:
        return ImportResult(
            platform, account_label, None,
            errors=["单次导入会话数超过上限 200"],
            warnings=warnings,
        )

    conversations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, (title, body_lines) in enumerate(blocks, start=1):
        where = f"第 {index} 个会话「{title[:24]}」"
        if not title:
            errors.append(f"{where}：标题为空")
            continue
        if len(title) > 255:
            errors.append(f"{where}：标题超过 255 字符")

        meta: dict[str, str] = {}
        sections: list[tuple[str, str | None, list[str]]] = []
        sender_label: str | None = None
        sender_time: str | None = None
        sender_body: list[str] = []
        in_body = False

        for line in body_lines:
            heading = MSG_HEADING.match(line)
            if heading:
                if sender_label is not None:
                    sections.append((sender_label, sender_time, sender_body))
                sender_label = heading.group(1).strip()
                sender_time = heading.group(2).strip() if heading.group(2) else None
                sender_body = []
                in_body = True
                continue
            if not in_body:
                meta_line = META_LINE.match(line)
                if meta_line and meta_line.group(1) in META_KEYS:
                    key = META_KEYS[meta_line.group(1)]
                    if key in ("platform", "account_label"):
                        continue
                    meta[key] = meta_line.group(2).strip()
                continue
            sender_body.append(line)
        if sender_label is not None:
            sections.append((sender_label, sender_time, sender_body))

        if not sections:
            errors.append(f"{where}：未找到「## 用户 / ## 助手」分段")
            continue

        started_at = parse_timestamp(meta.get("started_at"), "开始时间", errors, where)
        ended_at = parse_timestamp(meta.get("ended_at"), "结束时间", errors, where)
        if started_at and ended_at and ended_at < started_at:
            errors.append(f"{where}：结束时间早于开始时间")

        course_code = meta.get("course_code")
        if course_code and not COURSE_CODE_RE.match(course_code):
            warnings.append(f"{where}：课程编号「{course_code}」格式不合法，已忽略")
            course_code = None

        messages: list[dict[str, Any]] = []
        for section_index, (label, timestamp, body) in enumerate(sections, start=1):
            msg_where = f"{where} 第 {section_index} 段"
            sender = map_sender(label, errors, msg_where)
            content = clean_content("\n".join(body))
            if not content:
                warnings.append(f"{msg_where}：内容为空，已跳过")
                continue
            created_at = parse_timestamp(
                timestamp, "消息时间", errors, msg_where
            ) or started_at
            messages.append(
                {
                    "seq_no": len(messages) + 1,
                    "sender": sender,
                    "content": content,
                    "created_at": created_at,
                }
            )

        if not messages:
            errors.append(f"{where}：没有有效消息")
            continue
        if started_at is None:
            started_at = messages[0]["created_at"]

        external_id = meta.get("external_id")
        if external_id:
            external_id = external_id[:200]
        else:
            external_id = external_id_from(
                f"{title}|{messages[0]['content'][:200]}|{started_at or ''}"
            )
        if external_id in seen_ids:
            errors.append(f"{where}：external_id「{external_id}」在文件内重复")
            continue
        seen_ids.add(external_id)

        conversations.append(
            {
                "external_id": external_id,
                "title": title,
                "course_code": course_code,
                "started_at": started_at,
                "ended_at": ended_at,
                "tags": normalize_tags(meta.get("tags")),
                "messages": messages,
            }
        )

    check_limits(conversations, errors, max_messages=max_messages)
    return ImportResult(
        platform,
        account_label,
        None,
        conversations=conversations,
        errors=errors,
        warnings=warnings,
    )
