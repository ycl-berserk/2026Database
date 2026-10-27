from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.services import text2sql_eval_service as evals
from app.services import text2sql_service as t2s

ROOT = Path(__file__).resolve().parents[2]


def test_load_cases_reads_lists_and_rejects_missing_sections(tmp_path):
    cases = evals.load_cases(ROOT / "data" / "text2sql_eval.json")
    assert len(cases["questions"]) == 30
    assert len(cases["guard_cases"]) == 10
    assert len(cases["scope_cases"]) == 2

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"questions": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="guard_cases"):
        evals.load_cases(broken)


def test_canonical_ignores_row_order_and_uses_selected_columns():
    left = {
        "columns": ["kind", "total"],
        "rows": [{"kind": "note", "total": 2}, {"kind": "question", "total": 1}],
    }
    right = {
        "columns": ["kind", "total"],
        "rows": [{"kind": "question", "total": 1}, {"kind": "note", "total": 2}],
    }
    assert evals._canonical(left) == evals._canonical(right)

    reordered = {
        "columns": ["total", "kind"],
        "rows": [{"kind": "note", "total": 2}, {"kind": "question", "total": 1}],
    }
    assert evals._canonical(left) == evals._canonical(reordered)

    different = {
        "columns": ["kind", "total"],
        "rows": [{"kind": "note", "total": 3}, {"kind": "question", "total": 1}],
    }
    assert evals._canonical(left) != evals._canonical(different)


def test_scope_passes_checks_empty_and_forbidden_values():
    assert evals._scope_passes({"rows": []}, {"expect_empty": True})
    assert not evals._scope_passes({"rows": [{"x": 1}]}, {"expect_empty": True})

    leak = {"rows": [{"content": "bob 的私密批注"}]}
    assert not evals._scope_passes(
        leak, {"forbidden_values": ["bob 的私密批注"]}
    )
    assert evals._scope_passes(
        {"rows": [{"content": "alice 自己的批注"}]},
        {"forbidden_values": ["bob 的私密批注"]},
    )
    assert evals._scope_passes(leak, {})


def _result(rows: list[dict]) -> dict:
    return {
        "columns": list(rows[0]) if rows else ["x"],
        "rows": rows,
        "truncated": False,
    }


def test_evaluate_computes_accuracy_legality_and_security(monkeypatch):
    cases = {
        "questions": [
            {"id": "Q1", "question": "q1", "user": "alice", "gold_sql": "gold-ok"},
            {"id": "Q2", "question": "q2", "user": "alice", "gold_sql": "gold-diff"},
            {"id": "Q3", "question": "q3", "user": "alice", "gold_sql": "gold-error"},
        ],
        "guard_cases": [
            {"id": "G1", "sql": "evil", "reason": "DDL"},
            {"id": "G2", "sql": "safe", "reason": "正常"},
        ],
        "scope_cases": [
            {"id": "S1", "user": "alice", "sql": "scope-empty", "expect_empty": True},
            {
                "id": "S2",
                "user": "alice",
                "sql": "scope-leak",
                "forbidden_values": ["bob-content"],
            },
        ],
    }
    responses = {
        "gold-ok": _result([{"x": 1}, {"x": 2}]),
        "gen-ok": _result([{"x": 2}, {"x": 1}]),
        "gold-diff": _result([{"x": 1}]),
        "gen-diff": _result([{"x": 2}]),
    }

    def fake_run(_conn, sql, _user_id):
        if sql == "gold-error":
            raise RuntimeError("gold boom")
        return {"sql": sql, "plan": None, "result": responses[sql]}

    async def fake_generate(question):
        return {"q1": "gen-ok", "q2": "gen-diff"}[question], "测试", "mock"

    def fake_check(sql):
        if sql == "evil":
            raise t2s.GuardError("拒绝")
        return sql

    def fake_execute(_conn, sql, _user_id):
        if sql == "scope-empty":
            return _result([])
        return _result([{"content": "bob-content"}])

    monkeypatch.setattr(evals, "_user_ids", lambda _conn, _names: {"alice": 1})
    monkeypatch.setattr(evals, "_run_sql", fake_run)
    monkeypatch.setattr(evals.t2s, "generate_sql", fake_generate)
    monkeypatch.setattr(evals.t2s, "check_sql", fake_check)
    monkeypatch.setattr(evals.t2s, "execute_query", fake_execute)

    original_mode = settings.text2sql_mode
    report = evals.evaluate(cases, object(), object(), provider="mock")

    assert settings.text2sql_mode == original_mode
    assert report["provider"] == "mock"
    assert report["modes"] == {"mock": 2}
    assert report["questions"]["total"] == 3
    assert report["questions"]["matched"] == 1
    assert report["questions"]["accuracy"] == pytest.approx(0.3333, abs=1e-4)
    assert report["questions"]["gold_legal"] == 2
    assert report["questions"]["generated_legal"] == 2
    assert report["legality"] == {"gold_rate": 0.6667, "generated_rate": 0.6667}
    assert report["security"] == {"total": 4, "passed": 2, "rate": 0.5}
    assert [case["status"] for case in report["cases"]] == ["ok", "ok", "gold_error"]
    assert [case["blocked"] for case in report["guard_cases"]] == [True, False]
    assert [case["passed"] for case in report["scope_cases"]] == [True, False]
