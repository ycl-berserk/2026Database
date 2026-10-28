from __future__ import annotations

import json

from app.services.rls_experiment import summarize_plan


def test_summarize_plan_collects_nodes_and_filters():
    payload = [
        {
            "Plan": {
                "Node Type": "Sort",
                "Actual Rows": 3,
                "Shared Hit Blocks": 4,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Filter": "(id > 0)",
                        "Actual Rows": 3,
                    }
                ],
            },
            "Planning Time": 0.12,
            "Execution Time": 0.34,
        }
    ]
    summary = summarize_plan(payload)
    assert summary["node_types"] == ["Seq Scan", "Sort"]
    assert summary["filter_count"] == 1
    assert summary["filters"] == ["(id > 0)"]
    assert summary["has_subquery_scan"] is False
    assert summary["has_subplan"] is False
    assert summary["actual_rows"] == 3
    assert summary["shared_hit_blocks"] == 4
    assert summary["execution_ms"] == 0.34


def test_summarize_plan_detects_barrier_and_subplan():
    summary = summarize_plan(
        json.dumps(
            [
                {
                    "Plan": {
                        "Node Type": "Subquery Scan",
                        "Actual Rows": 1,
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "SubPlan Name": "SubPlan 1",
                                "Actual Rows": 1,
                            }
                        ],
                    },
                    "Planning Time": 1,
                    "Execution Time": 2,
                }
            ]
        )
    )
    assert summary["has_subquery_scan"] is True
    assert summary["has_subplan"] is True
    assert summary["execution_ms"] == 2.0
