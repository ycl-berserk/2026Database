from __future__ import annotations

import json

from app.importers import json_importer, markdown_importer
from app.importers.base import (
    check_limits,
    clean_content,
    map_sender,
    normalize_tags,
    parse_timestamp,
)


def _json_doc(**overrides) -> bytes:
    doc = {
        "platform": "qoder",
        "account_label": "alice@qoder",
        "exported_at": "2026-03-01T12:00:00+08:00",
        "conversations": [
            {
                "external_id": "db-100",
                "title": "事务与锁",
                "course_code": "DB",
                "started_at": "2026-03-01 10:00",
                "tags": "事务，锁",
                "messages": [
                    {
                        "role": "user",
                        "content": "什么是事务？",
                        "created_at": "2026-03-01T10:00:00+08:00",
                    },
                    {
                        "role": "assistant",
                        "content": "事务是一组原子操作。",
                        "created_at": "2026-03-01T10:00:30+08:00",
                    },
                ],
            }
        ],
    }
    doc.update(overrides)
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def test_json_parse_valid():
    result = json_importer.parse(_json_doc())
    assert result.ok
    assert result.platform == "qoder"
    assert result.account_label == "alice@qoder"
    assert result.message_count == 2
    conv = result.conversations[0]
    assert conv["external_id"] == "db-100"
    assert conv["course_code"] == "DB"
    assert conv["tags"] == ["事务", "锁"]
    assert conv["started_at"].endswith("+08:00")
    assert [m["seq_no"] for m in conv["messages"]] == [1, 2]
    summary = result.summary()
    assert summary["conversation_count"] == 1
    assert summary["course_codes"] == ["DB"]
    payload = result.payload({"warnings": result.warnings})
    assert payload["format_version"] == 1
    assert payload["conversations"][0]["external_id"] == "db-100"


def test_json_platform_override():
    result = json_importer.parse(
        _json_doc(platform="unknown"), platform_override="chatgpt", account_override="bob"
    )
    assert result.ok
    assert result.platform == "chatgpt"
    assert result.account_label == "bob"


def test_json_rejects_invalid_platform():
    result = json_importer.parse(_json_doc(platform="unknown"))
    assert not result.ok
    assert any("平台必须" in error for error in result.errors)


def test_json_rejects_missing_or_duplicate_ids():
    doc = json.loads(_json_doc())
    del doc["conversations"][0]["external_id"]
    missing = json_importer.parse(json.dumps(doc, ensure_ascii=False).encode())
    assert any("缺少 external_id" in error for error in missing.errors)

    doc = json.loads(_json_doc())
    doc["conversations"].append(dict(doc["conversations"][0]))
    duplicated = json_importer.parse(json.dumps(doc, ensure_ascii=False).encode())
    assert any("重复" in error for error in duplicated.errors)


def test_json_rejects_unknown_sender_and_bad_time():
    doc = json.loads(_json_doc())
    doc["conversations"][0]["messages"][0]["role"] = "teacher"
    doc["conversations"][0]["messages"][1]["created_at"] = "not-a-time"
    result = json_importer.parse(json.dumps(doc, ensure_ascii=False).encode())
    assert any("未知的消息发送者" in error for error in result.errors)
    assert any("无法解析" in error for error in result.errors)


def test_json_rejects_empty_and_oversized():
    empty = json_importer.parse(json.dumps({"platform": "qoder", "conversations": []}).encode())
    assert any("conversations" in error for error in empty.errors)

    oversized = json_importer.parse(_json_doc(), max_messages=1)
    assert any("超过上限" in error for error in oversized.errors)


def test_json_rejects_bad_utf8_and_bad_json():
    bad_utf8 = json_importer.parse(b"\xff\xfe\x00")
    assert "UTF-8" in bad_utf8.errors[0]
    bad_json = json_importer.parse(b"{not json")
    assert bad_json.errors and "JSON 解析失败" in bad_json.errors[0]


def test_json_root_array_is_accepted_with_warning():
    doc = json.loads(_json_doc())
    result = json_importer.parse(json.dumps(doc["conversations"], ensure_ascii=False).encode())
    assert any("数组" in warning for warning in result.warnings)
    assert "平台必须" in " ".join(result.errors)


MD_SAMPLE = """平台: qoder
账号: alice@qoder

# 进程调度

- 课程: OS
- 标签: 调度, 进程
- 开始: 2026-03-02 09:00

## 用户 [2026-03-02 09:00]
进程调度算法有哪些？

## 助手 [2026-03-02 09:00:30]
先来先服务、短作业优先、时间片轮转。
"""


def test_markdown_parse_valid():
    result = markdown_importer.parse(MD_SAMPLE.encode("utf-8"))
    assert result.ok
    assert result.platform == "qoder"
    assert result.account_label == "alice@qoder"
    conv = result.conversations[0]
    assert conv["title"] == "进程调度"
    assert conv["course_code"] == "OS"
    assert conv["tags"] == ["调度", "进程"]
    assert conv["external_id"].startswith("md-")
    assert [m["sender"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["started_at"].endswith("+08:00")


def test_markdown_requires_platform():
    text = MD_SAMPLE.replace("平台: qoder\n", "")
    result = markdown_importer.parse(text.encode("utf-8"))
    assert not result.ok
    assert any("来源平台" in error for error in result.errors)


def test_markdown_platform_override_and_empty_section():
    text = MD_SAMPLE.replace("平台: qoder\n", "") + "\n## 助手\n\n"
    result = markdown_importer.parse(text.encode(), platform_override="deepseek")
    assert result.platform == "deepseek"
    assert any("内容为空" in warning for warning in result.warnings)
    assert result.message_count == 2


def test_markdown_without_heading():
    result = markdown_importer.parse(b"just some text")
    assert not result.ok
    assert any("一级标题" in error for error in result.errors)


def test_markdown_external_id_is_deterministic():
    first = markdown_importer.parse(MD_SAMPLE.encode())
    second = markdown_importer.parse(MD_SAMPLE.encode())
    assert first.conversations[0]["external_id"] == second.conversations[0]["external_id"]


def test_timestamp_formats():
    errors: list[str] = []
    assert parse_timestamp(None, "时间", errors, "测试") is None
    assert parse_timestamp("", "时间", errors, "测试") is None
    assert parse_timestamp("2026-03-01", "时间", errors, "测试").startswith("2026-03-01T00:00:00")
    assert parse_timestamp("2026-03-01 10:30", "时间", errors, "测试").endswith("+08:00")
    assert parse_timestamp(1_770_000_000, "时间", errors, "测试").endswith("+08:00")
    assert not errors
    assert parse_timestamp("bad", "时间", errors, "测试") is None
    assert errors


def test_sender_mapping():
    errors: list[str] = []
    assert map_sender("我", errors, "x") == "user"
    assert map_sender("AI", errors, "x") == "assistant"
    assert map_sender(" SYSTEM ", errors, "x") == "system"
    assert map_sender("teacher", errors, "x") is None
    assert errors


def test_normalize_tags():
    assert normalize_tags("a，b、c;d") == ["a", "b", "c", "d"]
    assert normalize_tags(["x", "x", " y "]) == ["x", "y"]
    assert normalize_tags(None) == []
    assert normalize_tags(123) == []
    assert len(normalize_tags([str(i) for i in range(30)])) == 20
    assert normalize_tags(["x" * 41]) == []


def test_clean_content_and_limits():
    assert clean_content("a\r\nb\rc") == "a\nb\nc"
    assert clean_content(None) == ""
    errors: list[str] = []
    check_limits([{"title": "t", "messages": []}], errors, max_messages=10)
    assert any("没有任何消息" in error for error in errors)

    errors = []
    check_limits(
        [{"title": "t", "messages": [{"content": "x" * 11}]}],
        errors,
        max_messages=10,
        max_content=10,
    )
    assert any("超过 10 字符" in error for error in errors)
