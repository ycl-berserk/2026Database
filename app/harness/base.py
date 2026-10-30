from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..importers.base import ImportResult, clean_content

# 模型上下文、AGENTS.md 指令、斜杠命令都不是用户说的话，导入时必须剔除。
_INJECTED_PREFIXES = ("<", "# AGENTS.md", "[Request interrupted")
_SLASH_COMMAND = re.compile(
    r"^/(?:model|clear|exit|quit|help|compact|init|review|status)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class HarnessSession:
    harness: str
    session_id: str
    title: str
    path: Path
    updated_at: datetime | None
    external_id: str
    message_count: int = 0
    size_bytes: int = 0


class HarnessConnector(ABC):
    key: str
    label: str
    platform: str
    account_label: str

    @abstractmethod
    def root(self) -> Path | None:
        """返回数据目录；未安装或不可读时返回 None。"""

    @abstractmethod
    def list_sessions(self) -> list[HarnessSession]:
        """只做轻量元数据扫描，不解析消息正文。"""

    @abstractmethod
    def load(self, session: HarnessSession, *, max_messages: int) -> ImportResult:
        """把单个会话解析成统一导入结构，只保留对话文本。"""


def env_dir(name: str, *defaults: Path) -> Path | None:
    raw = (os.getenv(name) or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        return candidate if candidate.exists() else None
    for default in defaults:
        if default.expanduser().exists():
            return default.expanduser()
    return None


def is_injected(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return True
    if stripped.startswith(_INJECTED_PREFIXES):
        return True
    return bool(_SLASH_COMMAND.match(stripped.split("\n", 1)[0]))


def epoch_millis_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    seconds = value / 1000 if value > 100_000_000_000 else float(value)
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def make_message(sender: str, content: str, created_at: str | None) -> dict[str, Any] | None:
    body = clean_content(content)
    if not body:
        return None
    if sender == "user" and is_injected(body):
        return None
    return {"seq_no": 0, "sender": sender, "content": body, "created_at": created_at}


def build_result(
    connector: HarnessConnector,
    *,
    external_id: str,
    title: str,
    messages: list[dict[str, Any]],
    max_messages: int,
    started_at: str | None = None,
    ended_at: str | None = None,
    warnings: list[str] | None = None,
) -> ImportResult:
    notices = list(warnings or [])
    deduped: list[dict[str, Any]] = []
    for msg in messages:
        if (
            deduped
            and deduped[-1]["sender"] == msg["sender"]
            and deduped[-1]["content"] == msg["content"]
        ):
            continue
        deduped.append(msg)
    if len(deduped) > max_messages:
        notices.append(f"会话过长，仅导入前 {max_messages} 条消息")
        deduped = deduped[:max_messages]
    if not deduped:
        return ImportResult(
            connector.platform,
            connector.account_label,
            None,
            errors=["该会话没有可导入的对话文本（可能只包含工具调用）"],
            warnings=notices,
        )
    for index, msg in enumerate(deduped, start=1):
        msg["seq_no"] = index
    conversation = {
        "external_id": external_id[:200],
        "title": (title.strip() or "未命名会话")[:255],
        "course_code": None,
        "started_at": started_at or deduped[0]["created_at"],
        "ended_at": ended_at or deduped[-1]["created_at"],
        "tags": [],
        "messages": deduped,
    }
    return ImportResult(
        connector.platform,
        connector.account_label,
        None,
        conversations=[conversation],
        warnings=notices,
    )


def first_line(text: str, limit: int = 80) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def stat_updated_at(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def read_json_lines(path: Path, limit: int | None = None) -> Iterator[Any]:
    """逐行读取 JSONL；坏行直接跳过，避免单个残行毁掉整次导入。"""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if limit is not None and index >= limit:
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue
