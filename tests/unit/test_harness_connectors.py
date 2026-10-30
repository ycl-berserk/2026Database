from __future__ import annotations

import json
import sqlite3

import pytest

from app.harness import CONNECTORS
from app.harness.codex import CodexConnector
from app.harness.opencode import OpencodeConnector


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )


def _codex_session(tmp_path, session_id: str = "019dfd6b-ef8f-7c03-b156-eabec74e77d0"):
    root = tmp_path / "codex"
    path = root / "2026" / "05" / "06" / f"rollout-2026-05-06T21-13-22-{session_id}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-05-06T13:13:22Z",
                "payload": {"id": session_id, "cwd": "C:/work/demo", "cli_version": "0.1"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-06T13:13:23Z",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "system instructions"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-06T13:13:24Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-06T13:13:25Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "# AGENTS.md instructions for repo"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-06T13:13:26Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "MVCC 是怎么实现的？"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-06T13:13:30Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "通过版本链和快照实现多版本并发控制。"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-05-06T13:13:31Z",
                "payload": {"type": "function_call", "name": "shell", "arguments": "{}"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-05-06T13:13:26Z",
                "payload": {"type": "user_message", "message": "MVCC 是怎么实现的？"},
            },
        ],
    )
    return root, session_id


def test_codex_keeps_only_dialogue_text(tmp_path, monkeypatch):
    root, session_id = _codex_session(tmp_path)
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(root))
    connector = CodexConnector()

    sessions = connector.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == session_id
    assert sessions[0].title == "MVCC 是怎么实现的？"
    assert sessions[0].external_id == f"codex-{session_id}"

    result = connector.load(sessions[0], max_messages=100)
    assert result.ok, result.errors
    messages = result.conversations[0]["messages"]
    assert [(m["sender"], m["content"]) for m in messages] == [
        ("user", "MVCC 是怎么实现的？"),
        ("assistant", "通过版本链和快照实现多版本并发控制。"),
    ]
    assert result.conversations[0]["started_at"] == "2026-05-06T13:13:22Z"


def test_codex_truncates_long_sessions(tmp_path, monkeypatch):
    root, _ = _codex_session(tmp_path)
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(root))
    connector = CodexConnector()

    result = connector.load(connector.list_sessions()[0], max_messages=1)

    assert result.message_count == 1
    assert any("仅导入前 1 条" in warning for warning in result.warnings)


def test_codex_missing_root_returns_no_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("LS_HARNESS_CODEX_DIR", str(tmp_path / "does-not-exist"))

    assert CodexConnector().root() is None
    assert CodexConnector().list_sessions() == []


def _opencode_db(tmp_path):
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY, title TEXT, parent_id TEXT, time_archived INTEGER,
            time_created INTEGER, time_updated INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
            time_updated INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO session VALUES ('ses_main', 'GPU 蝴蝶计数', NULL, NULL, 1776432665000, 1776432867000)"
    )
    con.execute(
        "INSERT INTO session VALUES ('ses_child', 'subagent', 'ses_main', NULL, 1776432665000, 1776432867000)"
    )
    rows = [
        ("m1", 1776432665394, {"role": "user", "time": {"created": 1776432665394}}),
        ("m2", 1776432666000, {"role": "assistant", "time": {"created": 1776432666000}}),
    ]
    for message_id, created, data in rows:
        con.execute(
            "INSERT INTO message VALUES (?, 'ses_main', ?, ?, ?)",
            (message_id, created, created, json.dumps(data)),
        )
    parts = [
        ("p1", "m1", {"type": "text", "text": "蝴蝶计数怎么做？"}),
        ("p2", "m2", {"type": "step-start"}),
        ("p3", "m2", {"type": "reasoning", "text": "先想一下"}),
        ("p4", "m2", {"type": "tool", "tool": "read", "state": {"output": "SECRET=1"}}),
        ("p5", "m2", {"type": "text", "text": "可以用楔形枚举。"}),
    ]
    for part_id, message_id, data in parts:
        con.execute(
            "INSERT INTO part VALUES (?, ?, 'ses_main', 1776432666000, 1776432666000, ?)",
            (part_id, message_id, json.dumps(data)),
        )
    con.commit()
    con.close()
    return db


def test_opencode_reads_sqlite_text_parts(tmp_path, monkeypatch):
    db = _opencode_db(tmp_path)
    monkeypatch.setenv("LS_HARNESS_OPENCODE_DB", str(db))
    connector = OpencodeConnector()

    sessions = connector.list_sessions()
    assert [session.session_id for session in sessions] == ["ses_main"]
    assert sessions[0].title == "GPU 蝴蝶计数"
    assert sessions[0].message_count == 2

    result = connector.load(sessions[0], max_messages=100)
    assert result.ok, result.errors
    messages = result.conversations[0]["messages"]
    assert [(m["sender"], m["content"]) for m in messages] == [
        ("user", "蝴蝶计数怎么做？"),
        ("assistant", "可以用楔形枚举。"),
    ]


def test_opencode_missing_db_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("LS_HARNESS_OPENCODE_DB", str(tmp_path / "missing.db"))

    assert OpencodeConnector().list_sessions() == []


def _qoder_session(tmp_path, session_id: str = "ad141bc6-3d13-4f65-99b7-1891ab5079a9"):
    root = tmp_path / "qoder"
    path = root / "C--Users-demo" / f"{session_id}.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "ai-title", "sessionId": session_id, "aiTitle": "桌面应用四项优化调研"},
            {
                "type": "user",
                "timestamp": "2026-10-29T15:47:01Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": "SECRET=1", "is_error": False}
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-10-29T15:47:02Z",
                "message": {"role": "user", "content": "<command-name>/model</command-name>"},
            },
            {
                "type": "user",
                "timestamp": "2026-10-29T15:47:03Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "先看项目结构"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-10-29T15:47:04Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "内部推理"},
                        {"type": "tool_use", "name": "read", "input": {"path": ".env"}},
                        {"type": "text", "text": "项目分为 app、sql、desktop 三层。"},
                    ],
                },
            },
            {
                "type": "user",
                "isSidechain": True,
                "timestamp": "2026-10-29T15:47:05Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "子代理输入"}]},
            },
        ],
    )
    return root, session_id


def test_qoder_transcript_filters_tool_and_sidechain(tmp_path, monkeypatch):
    root, session_id = _qoder_session(tmp_path)
    monkeypatch.setenv("LS_HARNESS_QODER_DIR", str(root))
    connector = CONNECTORS["qoder"]

    sessions = connector.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == session_id
    assert sessions[0].title == "桌面应用四项优化调研"
    assert sessions[0].external_id == f"qoder-{session_id}"

    result = connector.load(sessions[0], max_messages=100)
    assert result.ok, result.errors
    messages = result.conversations[0]["messages"]
    assert [(m["sender"], m["content"]) for m in messages] == [
        ("user", "先看项目结构"),
        ("assistant", "项目分为 app、sql、desktop 三层。"),
    ]
    assert result.conversations[0]["ended_at"] == "2026-10-29T15:47:04Z"


def test_qoder_session_without_text_reports_error(tmp_path, monkeypatch):
    root = tmp_path / "qoder"
    path = root / "proj" / "only-tools.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]},
            }
        ],
    )
    monkeypatch.setenv("LS_HARNESS_QODER_DIR", str(root))

    result = CONNECTORS["qoder"].load(CONNECTORS["qoder"].list_sessions()[0], max_messages=10)

    assert not result.ok
    assert "没有可导入的对话文本" in result.errors[0]


@pytest.mark.parametrize(
    "text",
    ["<environment_context>", "# AGENTS.md instructions", "/model gpt-5", "[Request interrupted by user]"],
)
def test_injected_markers_are_detected(text):
    from app.harness.base import is_injected

    assert is_injected(text)


def test_normal_question_is_not_injected():
    from app.harness.base import is_injected

    assert not is_injected("帮我看看这个 SQL 为什么慢")
