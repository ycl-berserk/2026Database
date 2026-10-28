from __future__ import annotations

import pytest

from app.services import fsrs


def test_initial_intervals_follow_rating_order():
    intervals = {
        rating: fsrs.schedule(None, rating, 0)[1]
        for rating in ("again", "hard", "good", "easy")
    }
    assert intervals["again"] <= intervals["hard"] < intervals["good"] < intervals["easy"]
    assert intervals["good"] == 2
    assert intervals["easy"] == 8


def test_invalid_rating_is_rejected():
    with pytest.raises(ValueError):
        fsrs.normalize_rating("perfect")
    with pytest.raises(ValueError):
        fsrs.normalize_rating(3)  # type: ignore[arg-type]
    assert fsrs.normalize_rating(None) == "good"
    assert fsrs.normalize_rating(" EASY ") == "easy"


def test_recall_grows_stability_and_interval():
    state, interval = fsrs.schedule(None, "good", 0)
    next_state, next_interval = fsrs.review(state, "good", 1)
    assert next_state.stability > state.stability
    assert next_interval > interval
    assert next_state.lapses == 0


def test_easy_grows_more_than_good_and_hard():
    state, _ = fsrs.schedule(None, "good", 0)
    good_state, good_interval = fsrs.review(state, "good", 1)
    easy_state, easy_interval = fsrs.review(state, "easy", 1)
    hard_state, hard_interval = fsrs.review(state, "hard", 1)
    assert easy_state.stability > good_state.stability > hard_state.stability
    assert easy_interval > good_interval > hard_interval


def test_again_forgets_and_counts_lapse():
    state = fsrs.MemoryState(stability=20.0, difficulty=5.0, lapses=2)
    next_state, interval = fsrs.review(state, "again", 10)
    assert next_state.stability < state.stability
    assert next_state.lapses == 3
    assert 1 <= interval <= 3


def test_difficulty_stays_within_bounds():
    state = fsrs.MemoryState(stability=10.0, difficulty=9.9)
    for rating in ("again", "hard", "good", "hard", "again"):
        state, _ = fsrs.review(state, rating, 5)
        assert 1.0 <= state.difficulty <= 10.0
        assert state.stability >= fsrs.MIN_STABILITY


def test_retrievability_decreases_over_time():
    values = [fsrs.retrievability(days, 10.0) for days in (0, 1, 5, 30)]
    assert values[0] == pytest.approx(1.0, abs=1e-6)
    assert values == sorted(values, reverse=True)
    assert values[-1] < 0.8


def test_interval_respects_retention_and_cap():
    assert fsrs.interval_for(50.0, 0.9) >= fsrs.interval_for(50.0, 0.95)
    assert fsrs.interval_for(10_000.0, 0.9) == fsrs.MAX_INTERVAL
    assert fsrs.interval_for(0.01, 0.9) == 1
    state, interval = fsrs.schedule(None, "good", 0)
    assert interval == fsrs.interval_for(state.stability, 0.9)
