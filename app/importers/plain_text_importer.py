from __future__ import annotations

import re
from typing import Any

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

TITLE_RE = re.compile(r"^\s*(?:#\s+|标题\s*[:：]\s*)(?P<title>.+?)\s*$")
META_RE = re.compile(
    r"^\s*[-*]?\s*(?P<key>课程|标签|开始|开始时间|结束|结束时间)\s*[:：]\s*(?P<value>.+?)\s*$"
)
ROLE_RE = re.compile(
    r"^\s*(?P<role>我|用户|提问|助手|机器人|系统|human|user|you|assistant|"
    r"chatgpt|qoder|deepseek|claude|system)"
    r"(?:\s*(?:说|said))?\s*"
    r"(?P<bracket>[\[(（【][^\]\)）】]{4,40}[\])）】])?\s*[:：]\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
MSG_HEADING_RE = re.compile(r"^##\s+(?P<role>.+?)(?:\s*\[(?P<time>.+?)\])?\s*$")
LEADING_TIME_RE = re.compile(
    r"^\s*[\[(（【](?P<time>[^\]\)）】]{4,40})[\])）】]\s*(?P<rest>.*)$"
)
SEPARATOR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
META_KEYS = {
    "课程": "course_code",
    "标签": "tags",
    "开始": "started_at",
    "开始时间": "started_at",
    "结束": "ended_at",
    "结束时间": "ended_at",
}
PLATFORM_HINTS = ("qoder", "chatgpt", "deepseek", "claude")


def detect_platform(text: str) -> str:
    lowered = text.lower()
    for name in PLATFORM_HINTS:
        if name in lowered:
            return name
    return "other"


def _clean_label(value: str) -> str:
    label = value.replace("*", "").strip().rstrip(":：").strip()
    return re.sub(r"\s*(?:said|说)$", "", label, flags=re.IGNORECASE).strip()


def _strip_bracket(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("[]()（）【】") or None


def _derive_title(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        first_line = message["content"].strip().splitlines()[0] if message["content"].strip() else ""
        first_line = re.sub(r"^[#>*\-\s]+", "", first_line).strip()
        if first_line:
            return first_line[:80]
    return "粘贴的对话"


def parse(
    raw: bytes,
    *,
    title_override: str | None = None,
    platform_override: str | None = None,
    account_override: str | None = None,
    max_messages: int = 2000,
) -> ImportResult:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ImportResult("", "", None, errors=["文本不是有效的 UTF-8 编码"])

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ImportResult("", "", None, errors=["粘贴内容为空"])
    if len(text) > 200_000:
        return ImportResult("", "", None, errors=["粘贴内容超过 200,000 字符上限"])

    platform = (platform_override or detect_platform(text)).strip().lower()
    if platform not in PLATFORMS:
        errors.append("来源平台无效")
    account_label = (account_override or "paste").strip()[:100] or "paste"

    lines = text.split("\n")
    title = (title_override or "").strip()[:255] or None
    meta: dict[str, str] = {}
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    saw_message = False

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            if current is not None:
                current["body"].append("")
            continue
        if SEPARATOR_RE.match(line):
            if current is not None:
                current["body"].append("")
            continue

        heading = MSG_HEADING_RE.match(line)
        role_match = None if heading else ROLE_RE.match(line.replace("**", ""))
        if heading or role_match:
            if heading:
                role_label = heading.group("role")
                heading_time = heading.group("time")
                rest = ""
            else:
                assert role_match is not None
                role_label = role_match.group("role")
                heading_time = None
                rest = role_match.group("rest").strip()
            bracket = _strip_bracket(
                heading_time or (role_match.group("bracket") if role_match else None)
            )
            if rest:
                leading = LEADING_TIME_RE.match(rest)
                if leading and not bracket:
                    bracket = _strip_bracket(leading.group("time"))
                    rest = leading.group("rest").strip()

            if current is not None:
                sections.append(current)
            current = {
                "role": _clean_label(role_label),
                "time": bracket,
                "body": [rest] if rest else [],
                "line_no": line_no,
            }
            saw_message = True
            continue

        if current is not None:
            current["body"].append(raw_line.rstrip())
            continue

        title_match = TITLE_RE.match(line)
        if title_match and not title:
            title = title_match.group("title").strip()[:255]
            continue
        meta_match = META_RE.match(line)
        if meta_match:
            meta[META_KEYS[meta_match.group("key")]] = meta_match.group("value").strip()
            continue

    if current is not None:
        sections.append(current)

    messages: list[dict[str, Any]] = []
    first_message_at: str | None = None
    last_message_at: str | None = None
    for index, section in enumerate(sections, start=1):
        where = f"第 {section['line_no']} 行（第 {index} 段）"
        sender = map_sender(section["role"], errors, where)
        content = clean_content("\n".join(section["body"]))
        if not content:
            warnings.append(f"{where}：内容为空，已跳过")
            continue
        created_at = parse_timestamp(section["time"], "消息时间", errors, where)
        first_message_at = first_message_at or created_at
        if created_at:
            last_message_at = created_at
        messages.append(
            {
                "seq_no": len(messages) + 1,
                "sender": sender,
                "content": content,
                "created_at": created_at,
            }
        )

    if not saw_message and text:
        content = clean_content("\n".join(
            line for line in lines
            if not TITLE_RE.match(line.strip()) and not META_RE.match(line.strip())
        )) or text
        title = title or _derive_title([{"content": content}])
        messages = [{"seq_no": 1, "sender": "user", "content": content, "created_at": None}]
        warnings.append("未识别到「我 / 助手」等角色分隔，已作为一条用户消息保存")

    if not messages:
        errors.append("没有解析出任何消息")
        return ImportResult(platform, account_label, None, errors=errors, warnings=warnings)

    started_at = parse_timestamp(meta.get("started_at"), "开始时间", errors, "文本头")
    ended_at = parse_timestamp(meta.get("ended_at"), "结束时间", errors, "文本头")
    started_at = started_at or first_message_at
    ended_at = ended_at or last_message_at
    if started_at and ended_at and ended_at < started_at:
        errors.append("结束时间早于开始时间")
        ended_at = None

    course_code = meta.get("course_code")
    if course_code and not COURSE_CODE_RE.match(course_code):
        warnings.append(f"课程编号「{course_code}」格式不合法，已忽略")
        course_code = None

    title = title or _derive_title(messages)
    digest_seed = "paste|" + title + "|" + "\n".join(
        f"{message['sender']}:{message['content']}" for message in messages
    )
    conversation = {
        "external_id": external_id_from(digest_seed),
        "title": title,
        "course_code": course_code,
        "started_at": started_at,
        "ended_at": ended_at,
        "tags": normalize_tags(meta.get("tags")),
        "messages": messages,
    }
    conversations = [conversation]
    check_limits(conversations, errors, max_messages=max_messages)
    return ImportResult(
        platform,
        account_label,
        None,
        conversations=conversations,
        errors=errors,
        warnings=warnings,
    )
