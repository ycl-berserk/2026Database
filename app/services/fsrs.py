"""FSRS 风格的长程记忆调度。

公式与默认权重取自 FSRS-6；本项目只使用其中的长期调度部分：
同一天内的多次复习按 1 天间隔计算，避免在演示中把间隔算成 0。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp

RATINGS = {"again": 1, "hard": 2, "good": 3, "easy": 4}
DEFAULT_RATING = "good"
WEIGHTS = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    0.1542,
)
DECAY = -0.5
FACTOR = 19 / 81
MIN_STABILITY = 0.1
MAX_STABILITY = 36500.0
MAX_INTERVAL = 365
MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0


@dataclass(frozen=True)
class MemoryState:
    stability: float
    difficulty: float
    lapses: int = 0


def normalize_rating(value: str | None) -> str:
    if value is None or value == "":
        return DEFAULT_RATING
    if not isinstance(value, str):
        raise ValueError(f"不支持的复习评分：{value!r}")
    rating = value.strip().lower()
    if rating not in RATINGS:
        raise ValueError(f"不支持的复习评分：{value}")
    return rating


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def retrievability(elapsed_days: int, stability: float) -> float:
    if stability <= 0:
        return 0.0
    elapsed = max(0, int(elapsed_days))
    return (1 + FACTOR * elapsed / stability) ** DECAY


def initial_state(rating: str) -> MemoryState:
    grade = RATINGS[normalize_rating(rating)]
    stability = WEIGHTS[grade - 1]
    difficulty = _clamp(
        WEIGHTS[4] - exp(WEIGHTS[5] * (grade - 1)) + 1,
        MIN_DIFFICULTY,
        MAX_DIFFICULTY,
    )
    return MemoryState(round(stability, 4), round(difficulty, 4), 0)


def _next_difficulty(difficulty: float, grade: int) -> float:
    next_d = difficulty - WEIGHTS[6] * (grade - 3)
    target = initial_state("easy").difficulty
    next_d = WEIGHTS[7] * target + (1 - WEIGHTS[7]) * next_d
    return _clamp(next_d, MIN_DIFFICULTY, MAX_DIFFICULTY)


def interval_for(stability: float, retention: float = 0.9) -> int:
    desired = _clamp(retention, 0.7, 0.98)
    raw = stability / FACTOR * (desired ** (1 / DECAY) - 1)
    return int(_clamp(round(raw), 1, MAX_INTERVAL))


def review(
    state: MemoryState,
    rating: str,
    elapsed_days: int,
    retention: float = 0.9,
) -> tuple[MemoryState, int]:
    grade = RATINGS[normalize_rating(rating)]
    recall = retrievability(elapsed_days, state.stability)
    difficulty = _next_difficulty(state.difficulty, grade)

    if grade == 1:
        stability = (
            WEIGHTS[11]
            * state.difficulty ** (-WEIGHTS[12])
            * ((state.stability + 1) ** WEIGHTS[13] - 1)
            * exp(WEIGHTS[14] * (1 - recall))
        )
        lapses = state.lapses + 1
    else:
        hard_penalty = WEIGHTS[15] if grade == 2 else 1.0
        easy_bonus = WEIGHTS[16] if grade == 4 else 1.0
        growth = (
            exp(WEIGHTS[8])
            * (11 - difficulty)
            * state.stability ** (-WEIGHTS[9])
            * (exp(WEIGHTS[10] * (1 - recall)) - 1)
            * hard_penalty
            * easy_bonus
        )
        stability = state.stability * (1 + growth)
        lapses = state.lapses

    stability = _clamp(stability, MIN_STABILITY, MAX_STABILITY)
    next_state = MemoryState(round(stability, 4), round(difficulty, 4), lapses)
    return next_state, interval_for(stability, retention)


def schedule(
    state: MemoryState | None,
    rating: str,
    elapsed_days: int,
    retention: float = 0.9,
) -> tuple[MemoryState, int]:
    if state is None or state.stability <= 0:
        new_state = initial_state(rating)
        return new_state, interval_for(new_state.stability, retention)
    return review(state, rating, elapsed_days, retention)
