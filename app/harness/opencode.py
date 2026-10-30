from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..importers.base import ImportResult
from .base import (
    HarnessConnector,
    HarnessSession,
    build_result,
    epoch_millis_to_iso,
    first_line,
    make_message,
)

_DB_CANDIDATES = (
    Path("~/.local/share/opencode/opencode.db"),
    Path("~/AppData/Local/opencode/opencode.db"),
    Path("~/.opencode/opencode.db"),
)


class OpencodeConnector(HarnessConnector):
    key = "opencode"
    label = "opencode"
    platform = "opencode"
    account_label = "opencode"

    def root(self) -> Path | None:
        raw = (os.getenv("LS_HARNESS_OPENCODE_DB") or "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            return candidate if candidate.exists() else None
        for default in _DB_CANDIDATES:
            path = default.expanduser()
            if path.exists():
                return path
        return None

    def list_sessions(self) -> list[HarnessSession]:
        db = self.root()
        if db is None:
            return []
        sessions: list[HarnessSession] = []
        try:
            with _open_db(db) as con:
                rows = _session_rows(con)
                counts = _text_counts(con)
        except sqlite3.Error:
            return []
        for row in rows:
            session_id = str(row["id"])
            updated_at = epoch_millis_to_iso(row["time_updated"])
            sessions.append(
                HarnessSession(
                    harness=self.key,
                    session_id=session_id,
                    title=_clean_title(row["title"], session_id),
                    path=db,
                    updated_at=_parse_iso(updated_at),
                    external_id=f"{self.key}-{session_id}",
                    message_count=int(counts.get(session_id, 0)),
                    size_bytes=0,
                )
            )
        return sessions

    def load(self, session: HarnessSession, *, max_messages: int) -> ImportResult:
        db = self.root()
        if db is None:
            return ImportResult(
                self.platform, self.account_label, None, errors=["opencode 数据库不存在"]
            )
        messages: list[dict[str, Any]] = []
        started_at: str | None = None
        ended_at: str | None = None
        try:
            with _open_db(db) as con:
                rows = con.execute(
                    """
                    SELECT m.data AS message_data, p.data AS part_data
                      FROM message m
                      JOIN part p ON p.message_id = m.id
                     WHERE m.session_id = ?
                     ORDER BY m.time_created, p.time_created
                    """,
                    (session.session_id,),
                ).fetchall()
        except sqlite3.Error:
            return ImportResult(
                self.platform, self.account_label, None, errors=["无法读取 opencode 数据库"]
            )
        for row in rows:
            message_data = _json(row["message_data"])
            part_data = _json(row["part_data"])
            role = str(message_data.get("role") or "").lower()
            if role not in ("user", "assistant"):
                continue
            if part_data.get("type") != "text":
                continue
            created_at = epoch_millis_to_iso((message_data.get("time") or {}).get("created"))
            message = make_message(role, str(part_data.get("text") or ""), created_at)
            if message is None:
                continue
            messages.append(message)
            started_at = started_at or created_at
            if created_at:
                ended_at = created_at
        title = session.title
        if title.startswith("会话 "):
            derived = _first_user_line(messages)
            if derived:
                title = derived
        return build_result(
            self,
            external_id=session.external_id,
            title=title,
            messages=messages,
            max_messages=max_messages,
            started_at=started_at,
            ended_at=ended_at,
        )


@contextmanager
def _open_db(db: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        yield con
    finally:
        con.close()


def _session_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    # 子代理会话（parent_id 非空）不是用户自己的对话，列出来只会制造噪音。
    try:
        return con.execute(
            "SELECT id, title, time_created, time_updated FROM session "
            "WHERE time_archived IS NULL AND parent_id IS NULL "
            "ORDER BY time_updated DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return con.execute(
            "SELECT id, title, time_created, time_updated FROM session "
            "ORDER BY time_updated DESC"
        ).fetchall()


def _text_counts(con: sqlite3.Connection) -> dict[str, int]:
    try:
        rows = con.execute(
            "SELECT m.session_id, count(*) AS n "
            "FROM message m JOIN part p ON p.message_id = m.id "
            "WHERE json_extract(p.data, '$.type') = 'text' "
            "GROUP BY m.session_id"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["session_id"]): int(row["n"]) for row in rows}


def _json(value: Any) -> dict[str, Any]:
    if not isinstance(value, (str, bytes)):
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_title(title: Any, session_id: str) -> str:
    text = str(title or "").strip()
    if not text or text.lower().startswith("new session"):
        return f"会话 {session_id[-6:]}"
    return text[:255]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _first_user_line(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message["sender"] == "user":
            return first_line(message["content"])
    return ""


__all__ = ["OpencodeConnector"]
