from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8))
PLATFORMS = ("qoder", "chatgpt", "deepseek", "claude", "other")
SENDERS = {"user": "user", "human": "user", "me": "user", "我": "user", "用户": "user",
           "assistant": "assistant", "ai": "assistant", "bot": "assistant",
           "model": "assistant", "助手": "assistant",
           "system": "system", "系统": "system"}

COURSE_CODE_RE = re.compile(r"^[A-Za-z0-9-]{2,20}$")


@dataclass
class ImportResult:
    platform: str
    account_label: str
    exported_at: str | None
    conversations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.conversations)

    @property
    def message_count(self) -> int:
        return sum(len(conv["messages"]) for conv in self.conversations)

    def payload(self, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "format_version": 1,
            "platform": self.platform,
            "account_label": self.account_label,
            "exported_at": self.exported_at,
            "conversations": self.conversations,
            **meta,
        }

    def summary(self) -> dict[str, Any]:
        tags = sorted({tag for conv in self.conversations for tag in conv["tags"]})
        codes = sorted({c["course_code"] for c in self.conversations if c["course_code"]})
        return {
            "conversation_count": len(self.conversations),
            "message_count": self.message_count,
            "tag_count": len(tags),
            "tags": tags[:20],
            "course_codes": codes,
        }


def parse_timestamp(value: Any, field_name: str, errors: list[str], where: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value, CST)
        except (OverflowError, OSError, ValueError):
            errors.append(f"{where}：{field_name} 时间戳无效")
            return None
        return dt.isoformat()
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        normalized += "T00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", normalized):
        normalized = normalized.replace(" ", "T")
        if normalized.count(":") == 1:
            normalized += ":00"
        normalized += "+08:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{where}：无法解析 {field_name} 时间「{text}」")
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.isoformat()


def map_sender(value: Any, errors: list[str], where: str) -> str | None:
    key = str(value or "").strip().lower()
    sender = SENDERS.get(key)
    if sender is None:
        errors.append(f"{where}：未知的消息发送者「{value}」")
    return sender


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，、;；]", value)
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        return []
    seen: list[str] = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in seen and len(tag) <= 40:
            seen.append(tag)
    return seen[:20]


def external_id_from(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"md-{digest}"


def clean_content(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return text


def check_limits(conversations: list[dict], errors: list[str], *,
                 max_messages: int, max_content: int = 200_000) -> None:
    total = sum(len(conv["messages"]) for conv in conversations)
    if total == 0:
        errors.append("文件中没有任何消息")
    if total > max_messages:
        errors.append(f"消息总数 {total} 超过上限 {max_messages} 条")
    for conv in conversations:
        for msg in conv["messages"]:
            if len(msg["content"]) > max_content:
                errors.append(f"会话「{conv['title']}」存在超过 {max_content} 字符的消息")
                break
