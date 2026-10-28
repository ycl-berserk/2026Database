from __future__ import annotations

import pytest

from app.services.rls_experiment import run_experiment


def test_experiment_modes_agree_and_clean_up(conn, base):
    users = {"alice": base["users"]["alice"], "bob": base["users"]["bob"]}
    before = conn.execute(
        "SELECT count(*) AS n FROM app.conversations"
    ).fetchone()["n"]

    result = run_experiment(conn, users)

    assert result["summary"]["consistent"] is True
    assert result["unset_guc"] == {"view_rows": 0, "rls_rows": 0}
    assert all(result["cleanup"].values()), result["cleanup"]

    alice = result["users"]["alice"]
    bob = result["users"]["bob"]
    assert alice["expected_ids"] == sorted(base["conversations"].values())
    assert bob["expected_ids"] == sorted(
        [base["conversations"]["db-1"], base["conversations"]["os-1"]]
    )
    for item in (alice, bob):
        for mode in ("view", "where", "rls"):
            assert item["modes"][mode]["ids"] == item["expected_ids"]

    assert result["summary"]["row_counts"]["alice"] == {
        "expected": 3,
        "view": 3,
        "where": 3,
        "rls": 3,
    }
    assert result["summary"]["row_counts"]["bob"]["expected"] == 2

    view_plans = [alice["modes"]["view"]["plan"], bob["modes"]["view"]["plan"]]
    where_plans = [alice["modes"]["where"]["plan"], bob["modes"]["where"]["plan"]]
    rls_plans = [alice["modes"]["rls"]["plan"], bob["modes"]["rls"]["plan"]]
    assert all(plan["has_subquery_scan"] for plan in view_plans)
    assert not any(plan["has_subquery_scan"] for plan in where_plans + rls_plans)
    assert all(plan["filter_count"] > 0 for plan in where_plans + rls_plans)
    assert all(plan["actual_rows"] > 0 for plan in view_plans + where_plans + rls_plans)

    after = conn.execute("SELECT count(*) AS n FROM app.conversations").fetchone()["n"]
    assert after == before

    objects = result["experiment_objects"]
    assert (
        conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s",
            (objects["schema"],),
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (objects["role"],)
        ).fetchone()
        is None
    )


def test_experiment_requires_users(conn):
    with pytest.raises(ValueError):
        run_experiment(conn, {})
