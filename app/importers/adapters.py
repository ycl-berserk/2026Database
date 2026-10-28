"""把各平台原生导出映射为统一导入文档，不重复实现入库逻辑。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .base import external_id_from, normalize_tags

CHATGPT_ROLES = ("user", "assistant", "system")

MD_SENDER = re.compile(
    r"^(#{2,4})\s*(user|human|me|you|assistant|ai|model|deepseek|system)"
    r"\b\s*(?:\[(.+?)\])?\s*$",
    re.IGNORECASE,
)
MD_SENDER_LABELS = {
    "user": "用户",
    "human": "用户",
    "me": "用户",
    "you": "用户",
    "assistant": "助手",
    "ai": "助手",
    "model": "助手",
    "deepseek": "助手",
    "system": "系统",
}
MD_META = re.compile(
    r"^\s*[-*]?\s*\**\s*"
    r"(platform|account|course|course code|tags?|session id|started|ended)"
    r"\s*\**\s*[:：]\s*\**\s*(.+?)\s*$",
    re.IGNORECASE,
)
MD_META_LABELS = {
    "platform": "平台",
    "account": "账号",
    "course": "课程",
    "course code": "课程",
    "tag": "标签",
    "tags": "标签",
    "session id": "会话ID",
    "started": "开始",
    "ended": "结束",
}


def normalize_json(doc: Any) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    if isinstance(doc, list):
        if any(_looks_like_chatgpt(item) for item in doc):
            return _chatgpt_document(doc), ["已识别 ChatGPT conversations.json 导出格式"]
        return doc, warnings

    if not isinstance(doc, dict):
        return doc, warnings

    if _looks_like_chatgpt(doc):
        items = doc.get("conversations")
        if not isinstance(items, list):
            items = [doc]
        warnings.append("已识别 ChatGPT conversations.json 导出格式")
        return _chatgpt_document(items, doc), warnings

    chatgpt_items = doc.get("conversations")
    if isinstance(chatgpt_items, list) and any(
        _looks_like_chatgpt(item) for item in chatgpt_items
    ):
        warnings.append("已识别 ChatGPT conversations.json 导出格式")
        return _chatgpt_document(chatgpt_items, doc), warnings

    sessions = doc.get("sessions")
    if (
        isinstance(sessions, list)
        and sessions
        and any(isinstance(item, dict) for item in sessions)
    ):
        warnings.append("已识别 Qoder sessions 导出格式")
        return _sessions_document(doc, sessions), warnings

    conversations = doc.get("conversations")
    if isinstance(conversations, list) and any(
        isinstance(item, dict) and _needs_alias(item) for item in conversations
    ):
        warnings.append("已按平台字段别名映射为统一格式")
        return _sessions_document(doc, conversations), warnings

    return doc, warnings


def normalize_markdown(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    converted = 0
    out: list[str] = []
    for line in lines:
        sender = MD_SENDER.match(line)
        if sender:
            label = MD_SENDER_LABELS[sender.group(2).lower()]
            stamp = f" [{sender.group(3).strip()}]" if sender.group(3) else ""
            out.append(f"## {label}{stamp}")
            converted += 1
            continue
        meta = MD_META.match(line)
        if meta:
            out.append(f"- {MD_META_LABELS[meta.group(1).lower()]}: {meta.group(2).strip()}")
            continue
        out.append(line)

    normalized = "\n".join(out)
    if converted:
        warnings.append("已识别 User / Assistant 标题格式并映射为统一 Markdown")
    if not re.search(
        r"^\s*[-*]?\s*(?:平台|platform)\s*[:：]", normalized, re.IGNORECASE | re.MULTILINE
    ) and re.search(r"deepseek", normalized, re.IGNORECASE):
        normalized = "平台: deepseek\n" + normalized
    return normalized, warnings


def _looks_like_chatgpt(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("mapping"), dict)
        and any(isinstance(node, dict) for node in item["mapping"].values())
    )


def _needs_alias(conv: dict) -> bool:
    if "mapping" in conv or "chat_history" in conv or "history" in conv:
        return True
    if "session_id" in conv or "sessionId" in conv:
        return True
    if "name" in conv and "title" not in conv:
        return True
    messages = conv.get("messages")
    return bool(isinstance(messages, list) and any(isinstance(msg, dict) and "text" in msg and "content" not in msg for msg in messages))


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        chunks = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = _text(part)
                if text:
                    chunks.append(text)
        joined = "\n".join(chunk for chunk in chunks if chunk.strip())
        if joined.strip():
            return joined.strip()
    return _text(content.get("text"))


def _chatgpt_document(items: list, wrapper: dict | None = None) -> dict:
    conversations = []
    for item in items:
        if not isinstance(item, dict):
            continue
        conversation = _chatgpt_thread(item)
        if conversation is not None:
            conversations.append(conversation)
    wrapper = wrapper or {}
    return {
        "platform": "chatgpt",
        "account_label": str(
            wrapper.get("account_label") or wrapper.get("account") or "chatgpt-export"
        )[:100],
        "exported_at": _iso(wrapper.get("exported_at")),
        "conversations": conversations,
    }


def _chatgpt_thread(item: dict) -> dict | None:
    mapping = item.get("mapping") or {}
    path: list[dict] = []
    current = item.get("current_node")
    seen: set[str] = set()
    while current and current in mapping and current not in seen:
        seen.add(current)
        node = mapping[current]
        message = node.get("message")
        if isinstance(message, dict):
            path.append(message)
        current = node.get("parent")
    path.reverse()

    nodes = path or [
        node.get("message")
        for node in mapping.values()
        if isinstance(node, dict) and isinstance(node.get("message"), dict)
    ]
    nodes = [node for node in nodes if isinstance(node, dict)]
    if not path:
        nodes.sort(key=lambda message: message.get("create_time") or 0)

    messages: list[dict] = []
    for node in nodes:
        role = str((node.get("author") or {}).get("role") or "").lower()
        if role not in CHATGPT_ROLES:
            continue
        content = _content_text(node.get("content"))
        if not content:
            continue
        messages.append(
            {
                "role": role,
                "content": content,
                "created_at": _iso(node.get("create_time")),
            }
        )
    if not messages:
        return None

    started_at = _iso(item.get("create_time")) or messages[0]["created_at"]
    ended_at = _iso(item.get("update_time"))
    external_id = str(item.get("id") or item.get("conversation_id") or "").strip()
    if not external_id:
        external_id = external_id_from(
            f"{item.get('title') or ''}|{messages[0]['content'][:200]}|{started_at or ''}"
        )
    return {
        "external_id": external_id[:200],
        "title": str(item.get("title") or "未命名会话").strip()[:255],
        "course_code": None,
        "started_at": started_at,
        "ended_at": ended_at,
        "tags": normalize_tags(item.get("tags")),
        "messages": messages,
    }


def _sessions_document(doc: dict, sessions: list) -> dict:
    conversations = []
    for raw in sessions:
        if not isinstance(raw, dict):
            continue
        conversation = _aliased_conversation(raw)
        if conversation is not None:
            conversations.append(conversation)
    return {
        "platform": str(
            doc.get("platform") or doc.get("source") or doc.get("provider") or "qoder"
        ).strip().lower(),
        "account_label": str(
            doc.get("account_label") or doc.get("account") or "qoder-export"
        )[:100],
        "exported_at": _iso(doc.get("exported_at") or doc.get("exportedAt")),
        "conversations": conversations,
    }


def _aliased_conversation(raw: dict) -> dict | None:
    messages_raw = (
        raw.get("messages")
        or raw.get("chat_history")
        or raw.get("chatHistory")
        or raw.get("history")
        or []
    )
    if not isinstance(messages_raw, list):
        messages_raw = []

    messages: list[dict] = []
    for msg in messages_raw:
        if not isinstance(msg, dict):
            continue
        content = _text(msg.get("content")) or _text(msg.get("text")) or _text(msg.get("message"))
        if not content:
            continue
        role = msg.get("role") or msg.get("sender") or msg.get("author") or ""
        messages.append(
            {
                "role": role,
                "content": content,
                "created_at": _iso(
                    msg.get("created_at")
                    or msg.get("timestamp")
                    or msg.get("time")
                    or msg.get("createdAt")
                ),
            }
        )
    if not messages:
        return None

    external_id = str(
        raw.get("external_id")
        or raw.get("session_id")
        or raw.get("sessionId")
        or raw.get("id")
        or ""
    ).strip()
    title = str(raw.get("title") or raw.get("name") or "未命名会话").strip()[:255]
    if not external_id:
        external_id = external_id_from(
            f"{title}|{messages[0]['content'][:200]}|{messages[0]['created_at'] or ''}"
        )
    started_at = _iso(
        raw.get("started_at") or raw.get("created_at") or raw.get("createdAt") or raw.get("time")
    )
    ended_at = _iso(
        raw.get("ended_at") or raw.get("updated_at") or raw.get("updatedAt")
    )
    return {
        "external_id": external_id[:200],
        "title": title,
        "course_code": raw.get("course_code") or raw.get("course"),
        "started_at": started_at,
        "ended_at": ended_at,
        "tags": normalize_tags(raw.get("tags")),
        "messages": messages,
    }
