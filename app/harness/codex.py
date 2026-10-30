from __future__ import annotations

import re
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

_SESSION_ID = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.IGNORECASE
)
_TEXT_TYPES = ("input_text", "output_text")


class CodexConnector(HarnessConnector):
    key = "codex"
    label = "Codex CLI"
    platform = "codex"
    account_label = "codex-cli"

    def root(self) -> Path | None:
        return env_dir("LS_HARNESS_CODEX_DIR", Path("~/.codex/sessions"))

    def list_sessions(self) -> list[HarnessSession]:
        root = self.root()
        if root is None:
            return []
        sessions: list[HarnessSession] = []
        for path in root.glob("**/rollout-*.jsonl"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            session_id, cwd, title = _peek(path)
            if not session_id:
                match = _SESSION_ID.search(path.name)
                session_id = match.group(1) if match else path.stem
            fallback = Path(cwd).name if cwd else "Codex 会话"
            sessions.append(
                HarnessSession(
                    harness=self.key,
                    session_id=session_id,
                    title=title or fallback,
                    path=path,
                    updated_at=stat_updated_at(path),
                    external_id=f"{self.key}-{session_id}",
                    size_bytes=size,
                )
            )
        sessions.sort(
            key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return sessions

    def load(self, session: HarnessSession, *, max_messages: int) -> ImportResult:
        messages: list[dict[str, Any]] = []
        started_at: str | None = None
        ended_at: str | None = None
        for record in read_json_lines(session.path):
            if not isinstance(record, dict):
                continue
            kind = record.get("type")
            raw_payload = record.get("payload")
            payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
            if kind == "session_meta":
                stamp = record.get("timestamp") or payload.get("timestamp")
                if isinstance(stamp, str) and stamp:
                    started_at = stamp
                continue
            if kind != "response_item" or payload.get("type") != "message":
                continue
            role = str(payload.get("role") or "").lower()
            if role not in ("user", "assistant"):
                continue
            content = payload.get("content")
            if not isinstance(content, list):
                continue
            chunks = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") in _TEXT_TYPES
            ]
            body = "\n".join(chunk for chunk in chunks if chunk.strip())
            stamp = record.get("timestamp")
            created_at = str(stamp) if isinstance(stamp, str) and stamp else None
            message = make_message(role, body, created_at)
            if message is None:
                continue
            messages.append(message)
            if created_at:
                ended_at = created_at
        return build_result(
            self,
            external_id=session.external_id,
            title=session.title,
            messages=messages,
            max_messages=max_messages,
            started_at=started_at,
            ended_at=ended_at,
        )


def _peek(path: Path) -> tuple[str | None, str | None, str | None]:
    """只读文件头部：取会话 id、工作目录和第一条真实用户消息作为标题。"""
    session_id: str | None = None
    cwd: str | None = None
    title: str | None = None
    for record in read_json_lines(path, limit=80):
        if not isinstance(record, dict):
            continue
        raw_payload = record.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        if record.get("type") == "session_meta":
            session_id = str(payload.get("id") or "") or None
            cwd = str(payload.get("cwd") or "") or None
            continue
        if title is not None:
            continue
        if record.get("type") != "response_item" or payload.get("type") != "message":
            continue
        if str(payload.get("role") or "").lower() != "user":
            continue
        content = payload.get("content")
        if not isinstance(content, list):
            continue
        body = "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") in _TEXT_TYPES
        ).strip()
        if not body or is_injected(body):
            continue
        title = first_line(body) or None
    return session_id, cwd, title


__all__ = ["CodexConnector"]
