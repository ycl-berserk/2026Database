from __future__ import annotations

import json

import pytest

from app.services import retrieval_eval_service


def test_rank_metrics_counts_recall_and_reciprocal_rank():
    metrics = retrieval_eval_service.rank_metrics([5, 1, 2], [1, 9], k=3)
    assert metrics == {
        "recall": 0.5,
        "mrr": 0.5,
        "first_rank": 2,
        "hits": 1,
    }


def test_rank_metrics_misses_outside_top_k():
    metrics = retrieval_eval_service.rank_metrics([5, 6, 7], [7], k=2)
    assert metrics == {
        "recall": 0.0,
        "mrr": 0.0,
        "first_rank": None,
        "hits": 0,
    }


def test_load_cases_normalizes_and_validates(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {"cases": [{"query": " 事务 ", "expected": ["db-1"]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases = retrieval_eval_service.load_cases(path)
    assert cases == [{"id": "case-1", "query": "事务", "expected": ["db-1"]}]

    path.write_text(json.dumps({"cases": [{"query": "事务"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="缺少"):
        retrieval_eval_service.load_cases(path)
