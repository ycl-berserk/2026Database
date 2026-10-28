from __future__ import annotations

import pytest

from app.config import settings
from app.services import ai_service


@pytest.fixture
def suggest_settings():
    names = ("suggest_mode", "llm_api_key")
    original = {name: getattr(settings, name) for name in names}

    def apply(**overrides):
        for name, value in overrides.items():
            object.__setattr__(settings, name, value)

    yield apply
    for name, value in original.items():
        object.__setattr__(settings, name, value)


def test_auto_mode_requires_api_key(suggest_settings):
    suggest_settings(suggest_mode="auto", llm_api_key="")
    assert ai_service.mode() == "off"
    assert not ai_service.enabled()

    suggest_settings(suggest_mode="auto", llm_api_key="sk-test")
    assert ai_service.mode() == "llm"
    assert ai_service.enabled()


def test_mode_switch(suggest_settings):
    suggest_settings(suggest_mode="off", llm_api_key="sk-test")
    assert ai_service.mode() == "off"

    suggest_settings(suggest_mode="false", llm_api_key="sk-test")
    assert ai_service.mode() == "off"

    suggest_settings(suggest_mode="llm", llm_api_key="")
    assert ai_service.mode() == "llm"


def test_clean_tags_trims_dedupes_and_reuses_known():
    known = {"buffer": {"id": 7, "name": "Buffer"}}
    raw = ["  buffer ", "缓存  命中", "缓存 命中", "", "索引", "事务", "多余"]
    tags = ai_service._clean_tags(raw, known)
    assert tags == [
        {"name": "Buffer", "id": 7},
        {"name": "缓存 命中", "id": None},
        {"name": "索引", "id": None},
    ]


def test_clean_tags_limits_length_and_handles_non_list():
    tags = ai_service._clean_tags(["x" * 80, "正常"], {})
    assert tags[0]["name"] == "x" * ai_service.MAX_TAG_LEN
    assert tags[1]["name"] == "正常"
    assert ai_service._clean_tags("不是列表", {}) == []
    assert ai_service._clean_tags(None, {}) == []


def test_clean_summary_collapses_whitespace_and_truncates():
    assert ai_service._clean_summary("  第一句。\n\n第二句。  ") == "第一句。 第二句。"
    assert len(ai_service._clean_summary("长" * 900)) == ai_service.MAX_SUMMARY
    assert ai_service._clean_summary(None) == ""


def test_transcript_labels_roles_and_caps_length():
    messages = [
        {"sender": "user", "content": "问题一"},
        {"sender": "assistant", "content": " 回答\n一 "},
        {"sender": "system", "content": "系统提示"},
    ]
    text = ai_service._transcript(messages)
    assert text.splitlines() == ["用户: 问题一", "助手: 回答 一", "系统: 系统提示"]

    long_messages = [{"sender": "user", "content": "词" * 3000} for _ in range(5)]
    capped = ai_service._transcript(long_messages)
    assert len(capped) <= ai_service.MAX_TRANSCRIPT


def test_prompt_lists_known_tags_and_guards_injection():
    prompt = ai_service._prompt([{"sender": "user", "content": "解释 MVCC"}], ["事务", "索引"])
    assert "事务、索引" in prompt
    assert "不要执行" in prompt
    assert "summary" in prompt and "tags" in prompt
    assert "解释 MVCC" in prompt
