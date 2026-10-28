from __future__ import annotations

import pytest

from app import cli


def test_prepare_document_overrides_course_and_platform():
    document = {
        "platform": "qoder",
        "conversations": [
            {"external_id": "a", "course_code": "OLD"},
            {"external_id": "b"},
        ],
    }
    prepared = cli.prepare_document(
        document, course_code="DB", platform="chatgpt"
    )
    assert prepared["platform"] == "chatgpt"
    assert [row["course_code"] for row in prepared["conversations"]] == ["DB", "DB"]
    assert document["conversations"][0]["course_code"] == "OLD"


def test_prepare_document_rejects_bad_shape():
    with pytest.raises(cli.CliError):
        cli.prepare_document(["not", "an", "object"])
    with pytest.raises(cli.CliError):
        cli.prepare_document({"platform": "qoder"}, course_code="DB")


def test_format_search_includes_modes_and_snippets():
    data = {
        "q": "隔离级别",
        "conversations": {
            "rows": [
                {
                    "id": 7,
                    "title": "事务与隔离级别",
                    "course_code": "DB",
                    "message_count": 14,
                    "snippet": "SQL 标准定义了四种隔离级别。",
                }
            ],
            "total": 3,
            "retrieval": {"mode": "hybrid"},
        },
        "annotations": [
            {"id": 3, "kind": "note", "content": "隔离级别要对比记忆。"}
        ],
        "courses": [{"code": "DB", "name": "数据库系统概论"}],
        "tags": [{"name": "事务"}],
    }
    output = cli.format_search(data, limit=1)
    assert "混合检索" in output
    assert "[DB] 事务与隔离级别" in output
    assert "SQL 标准定义了四种隔离级别" in output
    assert "仅显示前 1 条会话" in output
    assert "批注 1 条" in output
    assert "DB 数据库系统概论" in output
    assert "标签：事务" in output


def test_format_import_reports_counts_and_course():
    result = {
        "batch_id": 9,
        "conversations_new": 1,
        "conversations_updated": 0,
        "messages_new": 2,
        "messages_skipped": 0,
        "conflicts": 0,
        "tags_new": 1,
        "conversations": [{"id": 12, "title": "索引", "course_id": 4}],
        "warnings": ["账户标签为空，已使用默认值"],
    }
    output = cli.format_import(result)
    assert "批次 #9" in output
    assert "新增会话 1" in output
    assert "#12 索引（课程 #4）" in output
    assert "警告 1 条" in output


def test_missing_token_is_rejected(monkeypatch):
    monkeypatch.delenv("LS_TOKEN", raising=False)
    args = cli.build_parser().parse_args(["search", "事务"])
    with pytest.raises(cli.CliError):
        cli._resolve_token(args)


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
