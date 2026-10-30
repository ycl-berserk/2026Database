from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..importers.base import ImportResult
from .base import (
    HarnessConnector,
    HarnessSession,
    build_result,
    env_dir,
    first_line,
    is_injected,
    make_message,
    read_json_lines,
    stat_updated_at,
)

# Qoder CLI 沿用与 Claude Code 相同的 transcript 结构：
# projects/<sanitized-cwd>/<session-id>.jsonl，一行一个事件。
_TEXT_ROLES = ("user", "assistant")
_EPOCH = datetime.min.replace(tzinfo=UTC)


class AgentTranscriptConnector(HarnessConnector):
    """agent transcript 类 harness（Qoder、Claude Code 等）的通用读取器。"""

    def __init__(
        self,
        *,
        key: str,
        label: str,
        platform: str,
        account_label: str,
        env_name: str,
        defaults: tuple[Path, ...],
    ) -> None:
        self.key = key
        self.label = label
        self.platform = platform
        self.account_label = account_label
        self._env_name = env_name
        self._defaults = defaults

    def root(self) -> Path | None:
        return env_dir(self._env_name, *self._defaults)

    def list_sessions(self) -> list[HarnessSession]:
        root = self.root()
        if root is None:
            return []
        sessions: list[HarnessSession] = []
        for path in root.glob("*/*.jsonl"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            session_id = path.stem
            sessions.append(
                HarnessSession(
                    harness=self.key,
                    session_id=session_id,
                    title=_peek_title(path) or f"会话 {session_id[:8]}",
                    path=path,
                    updated_at=stat_updated_at(path),
                    external_id=f"{self.key}-{session_id}",
                    size_bytes=size,
                )
            )
        sessions.sort(
            key=lambda item: item.updated_at or _EPOCH,
            reverse=True,
        )
        return sessions

    def load(self, session: HarnessSession, *, max_messages: int) -> ImportResult:
        messages: list[dict[str, Any]] = []
        title: str | None = None
        started_at: str | None = None
        ended_at: str | None = None
        for record in read_json_lines(session.path):
            if not isinstance(record, dict):
                continue
            kind = record.get("type")
            if kind == "ai-title":
                value = record.get("aiTitle")
                if isinstance(value, str) and value.strip():
                    title = value.strip()
                continue
            if kind not in _TEXT_ROLES or record.get("isSidechain"):
                continue
            payload = record.get("message")
            if not isinstance(payload, dict):
                continue
            role = str(payload.get("role") or kind).lower()
            if role not in _TEXT_ROLES:
                continue
            stamp = record.get("timestamp")
            created_at = stamp if isinstance(stamp, str) and stamp else None
            message = make_message(role, _content_text(payload.get("content")), created_at)
            if message is None:
                continue
            messages.append(message)
            started_at = started_at or created_at
            if created_at:
                ended_at = created_at
        return build_result(
            self,
            external_id=session.external_id,
            title=title or session.title,
            messages=messages,
            max_messages=max_messages,
            started_at=started_at,
            ended_at=ended_at,
        )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def _peek_title(path: Path) -> str | None:
    """只看文件头部；ai-title 可能由模型生成在更后面，取不到就用第一句用户输入。"""
    first_user: str | None = None
    for record in read_json_lines(path, limit=150):
        if not isinstance(record, dict):
            continue
        if record.get("type") == "ai-title":
            value = record.get("aiTitle")
            if isinstance(value, str) and value.strip():
                return value.strip()[:255]
            continue
        if first_user is not None:
            continue
        if record.get("type") != "user" or record.get("isSidechain"):
            continue
        payload = record.get("message")
        if not isinstance(payload, dict):
            continue
        body = _content_text(payload.get("content")).strip()
        if not body or is_injected(body):
            continue
        first_user = first_line(body) or None
    return first_user


__all__ = ["AgentTranscriptConnector"]
