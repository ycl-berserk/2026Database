from __future__ import annotations

import json
from pathlib import Path

from app.importers import json_importer, markdown_importer

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def _parse_json(name: str, raw: dict | list | bytes):
    if isinstance(raw, bytes):
        payload = raw
    else:
        payload = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    return json_importer.parse(payload)


def test_chatgpt_mapping_export_follows_current_node():
    result = _parse_json(
        "chatgpt_export_conversations.json",
        (SAMPLES / "chatgpt_export_conversations.json").read_bytes(),
    )
    assert result.ok, result.errors
    assert result.platform == "chatgpt"
    assert len(result.conversations) == 2
    assert result.message_count == 28
    contents = " ".join(
        msg["content"] for conv in result.conversations for msg in conv["messages"]
    )
    assert "没有被采用的重新生成回答" not in contents
    first = result.conversations[0]
    assert first["external_id"] == "db-01-01"
    assert first["messages"][0]["created_at"].endswith("+00:00")
    assert any("ChatGPT" in warning for warning in result.warnings)


def test_chatgpt_export_detected_inside_conversations_wrapper():
    document = {
        "account": "alice@chatgpt",
        "conversations": [
            {
                "id": "c-1",
                "title": "包装形式",
                "create_time": 1770000000,
                "mapping": {
                    "root": {
                        "id": "root",
                        "parent": None,
                        "children": ["n1"],
                        "message": None,
                    },
                    "n1": {
                        "id": "n1",
                        "parent": "root",
                        "children": ["n2"],
                        "message": {
                            "author": {"role": "user"},
                            "content": {"content_type": "text", "parts": ["问题"]},
                            "create_time": 1770000001,
                        },
                    },
                    "n2": {
                        "id": "n2",
                        "parent": "n1",
                        "children": [],
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {
                                "content_type": "multimodal_text",
                                "parts": [{"text": "回答"}],
                            },
                            "create_time": 1770000002,
                        },
                    },
                },
                "current_node": "n2",
            }
        ],
    }
    result = _parse_json("wrapper.json", document)
    assert result.ok, result.errors
    assert result.platform == "chatgpt"
    assert result.account_label == "alice@chatgpt"
    assert result.conversations[0]["messages"] == [
        {"seq_no": 1, "sender": "user", "content": "问题", "created_at": "2026-02-02T02:40:01+00:00"},
        {"seq_no": 2, "sender": "assistant", "content": "回答", "created_at": "2026-02-02T02:40:02+00:00"},
    ]


def test_qoder_sessions_export_maps_aliases():
    result = _parse_json(
        "qoder_sessions_export.json",
        (SAMPLES / "qoder_sessions_export.json").read_bytes(),
    )
    assert result.ok, result.errors
    assert result.platform == "qoder"
    assert result.account_label == "alice@qoder"
    assert len(result.conversations) == 2
    assert result.message_count == 28
    assert {conv["course_code"] for conv in result.conversations} == {"OS"}
    assert result.conversations[0]["messages"][0]["sender"] == "user"
    assert any("Qoder" in warning for warning in result.warnings)


def test_deepseek_markdown_export_normalizes_headings_and_meta():
    result = markdown_importer.parse((SAMPLES / "deepseek_export.md").read_bytes())
    assert result.ok, result.errors
    assert result.platform == "deepseek"
    assert result.account_label == "alice@deepseek-export"
    assert len(result.conversations) == 2
    assert result.message_count == 28
    first = result.conversations[0]
    assert first["course_code"] == "NET"
    assert first["tags"]
    assert first["messages"][0]["sender"] == "user"
    assert first["messages"][1]["sender"] == "assistant"
    assert any("User / Assistant" in warning for warning in result.warnings)


def test_plain_markdown_without_platform_stays_unresolved():
    body = "# 会话\n## 用户\n问题\n## 助手\n回答\n".encode("utf-8")
    result = markdown_importer.parse(body)
    assert not result.ok
    assert any("无法确定来源平台" in error for error in result.errors)


def test_unified_document_is_not_rewritten_by_adapters():
    result = _parse_json(
        "qoder_db.json", (SAMPLES / "qoder_db.json").read_bytes()
    )
    assert result.ok, result.errors
    assert len(result.conversations) == 6
    assert result.message_count == 84
    assert not any("已识别" in warning for warning in result.warnings)
