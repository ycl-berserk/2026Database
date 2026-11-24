from __future__ import annotations

import pytest

from app.config import settings
from app.services import tutor_service


@pytest.fixture
def tutor_settings():
    names = ("tutor_mode", "llm_api_key")
    original = {name: getattr(settings, name) for name in names}

    def apply(**values):
        for name, value in values.items():
            object.__setattr__(settings, name, value)

    yield apply

    for name, value in original.items():
        object.__setattr__(settings, name, value)


def test_mode_auto_requires_key(tutor_settings):
    tutor_settings(tutor_mode="auto", llm_api_key="")
    assert tutor_service.mode() == "off"
    tutor_settings(tutor_mode="auto", llm_api_key="sk-test")
    assert tutor_service.mode() == "llm"


def test_mode_switch(tutor_settings):
    tutor_settings(tutor_mode="off", llm_api_key="sk-test")
    assert tutor_service.mode() == "off"
    tutor_settings(tutor_mode="llm", llm_api_key="")
    assert tutor_service.mode() == "llm"


def test_offline_score_rewards_coverage():
    reference = "SQL 标准定义了读未提交、读已提交、可重复读和串行化四种隔离级别。"
    strong = tutor_service.offline_score(reference, reference, ["四种隔离级别"])
    weak = tutor_service.offline_score("不知道", reference, ["四种隔离级别"])
    assert strong[0] > weak[0]
    assert strong[0] >= 8
    assert weak[0] <= 3


def test_offline_score_penalises_short_answers():
    reference = "索引通过 B+ 树减少磁盘 I/O，从而加速范围查询。"
    score, feedback = tutor_service.offline_score("索引", reference, ["B+ 树"])
    assert score <= 3
    assert "离线规则评分" in feedback


def test_offline_question_adapts_to_mastery():
    tag = {"name": "事务", "score": 3}
    context = {
        "snippets": [
            {"title": "事务", "sender": "assistant", "content": "事务具有 ACID 四个特性。"}
        ]
    }
    basic = tutor_service.offline_question(tag, context)
    assert "解释「事务」" in basic["question"]
    assert basic["reference_answer"].startswith("事务具有 ACID")

    advanced = tutor_service.offline_question({"name": "事务", "score": 9}, context)
    assert "适用场景" in advanced["question"]


def test_key_points_fall_back_to_name():
    points = tutor_service._key_points_from("", "隔离级别")
    assert points == ["隔离级别"]


def test_json_object_accepts_fenced_output():
    data = tutor_service._json_object('```json\n{"score": 7, "feedback": "ok"}\n```')
    assert data["score"] == 7


def test_mastery_summary():
    rows = [
        {"score": 3, "name": "A"},
        {"score": 7, "name": "B"},
        {"score": 9, "name": "C"},
    ]
    summary = tutor_service.mastery_summary(rows)
    assert summary["count"] == 3
    assert summary["average"] == 6.3
    assert summary["weakest"]["name"] == "A"
    assert summary["strongest"]["name"] == "C"
    assert tutor_service.mastery_summary([])["average"] is None
