from __future__ import annotations

from app.services.stats_experiment import run_experiment


def test_materialized_view_matches_live_and_tracks_refresh(conn, base):
    before = conn.execute(
        "SELECT (SELECT count(*) FROM app.courses) AS courses,"
        " (SELECT count(*) FROM app.messages) AS messages"
    ).fetchone()

    result = run_experiment(conn, user_id=base["users"]["alice"], runs=5)

    assert result["consistent"] is True
    assert result["stale_detected"] is True
    assert result["fresh_after_refresh"] is True
    assert result["rows"]["live"] == result["rows"]["materialized"] == before["courses"]
    assert result["timings_ms"]["materialized_median"] > 0
    assert result["timings_ms"]["refresh"] > 0
    assert result["cleanup"] == {"schema_dropped": True}

    live_types = result["plans"]["live"]["node_types"]
    materialized_types = result["plans"]["materialized"]["node_types"]
    assert "Aggregate" in live_types
    assert "Aggregate" not in materialized_types

    after = conn.execute(
        "SELECT (SELECT count(*) FROM app.courses) AS courses,"
        " (SELECT count(*) FROM app.messages) AS messages"
    ).fetchone()
    assert after == before

    assert (
        conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s",
            (result["experiment_schema"],),
        ).fetchone()
        is None
    )
