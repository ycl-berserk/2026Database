from __future__ import annotations

from app.importers import plain_text_importer


def test_parse_chinese_roles_meta_and_times():
    text = """# MVCC 复习

- 课程: DB
- 标签: MVCC, 事务隔离
- 开始: 2026-03-01 09:00

我：MVCC 是什么？
助手：多版本并发控制，让读写互不阻塞。

用户 [2026-03-01 09:02]：它和锁是什么关系？
ChatGPT：普通读不加锁，写操作仍然加行锁。
"""
    result = plain_text_importer.parse(text.encode("utf-8"), platform_override="qoder")
    assert result.ok
    conv = result.conversations[0]
    assert result.platform == "qoder"
    assert conv["title"] == "MVCC 复习"
    assert conv["course_code"] == "DB"
    assert conv["tags"] == ["MVCC", "事务隔离"]
    assert [m["sender"] for m in conv["messages"]] == [
        "user", "assistant", "user", "assistant"
    ]
    assert conv["messages"][2]["created_at"].endswith("+08:00")
    assert conv["started_at"].endswith("+08:00")


def test_parse_english_roles_and_heading_time():
    text = """Human: What is a covering index?
Assistant: An index that contains all columns needed by a query.

## Human [2026-04-02 10:30]
Why does it avoid table lookups?
## Assistant
Because the index itself answers the query.
"""
    result = plain_text_importer.parse(text.encode())
    assert result.ok
    assert result.platform == "other"
    assert [m["sender"] for m in result.conversations[0]["messages"]] == [
        "user", "assistant", "user", "assistant"
    ]
    assert result.conversations[0]["messages"][2]["created_at"]


def test_parse_plain_text_falls_back_to_single_user_message():
    result = plain_text_importer.parse("只有一段没有角色标记的笔记。".encode())
    assert result.ok
    conv = result.conversations[0]
    assert len(conv["messages"]) == 1
    assert conv["messages"][0]["sender"] == "user"
    assert any("未识别到" in warning for warning in result.warnings)


def test_parse_is_deterministic_and_respects_title_override():
    raw = "我：问题\n助手：回答".encode()
    first = plain_text_importer.parse(raw, title_override="自定义标题")
    second = plain_text_importer.parse(raw, title_override="自定义标题")
    assert first.conversations[0]["title"] == "自定义标题"
    assert (
        first.conversations[0]["external_id"]
        == second.conversations[0]["external_id"]
    )


def test_parse_rejects_empty_bad_time_and_too_many_messages():
    assert not plain_text_importer.parse(b"  \n").ok

    bad_time = plain_text_importer.parse(
        "我 [不是时间]：问题\n助手：回答".encode()
    )
    assert any("无法解析" in error for error in bad_time.errors)

    oversized = plain_text_importer.parse(
        "我：一\n助手：二\n我：三".encode(), max_messages=2
    )
    assert any("超过上限" in error for error in oversized.errors)
