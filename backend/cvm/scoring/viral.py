"""Heuristic viral-potential score (0-100) from snapshot history.

Components (each 0-1): rank (position in source list), velocity (metric growth per hour),
engagement ratio, cross-source presence, recency. Weights are tunable; an LLM rubric and a
LightGBM model trained on our own published-video results come in later phases.
"""

import math
from datetime import datetime

WEIGHTS = {"rank": 0.25, "velocity": 0.30, "engagement": 0.15, "cross_source": 0.20, "recency": 0.10}


def rank_component(rank: int | None, list_size: int = 50) -> float:
    if not rank:
        return 0.0
    return max(0.0, 1 - (rank - 1) / list_size)


def velocity_component(snapshots: list[tuple[datetime, float | None]]) -> float:
    """Relative growth per hour between the two latest snapshots, squashed to 0-1."""
    pts = [(t, m) for t, m in snapshots if m is not None]
    if len(pts) < 2:
        return 0.3  # unknown: neutral-low
    (t0, m0), (t1, m1) = sorted(pts)[-2:]
    hours = max((t1 - t0).total_seconds() / 3600, 0.25)
    growth = (m1 - m0) / max(m0, 1) / hours
    return 1 / (1 + math.exp(-4 * growth))  # 0 growth -> 0.5


def engagement_component(metric: float | None, engagement: float | None) -> float:
    if not metric or engagement is None:
        return 0.0
    return min(1.0, (engagement / metric) / 0.05)  # 5% engagement = max


def cross_source_component(n_sources: int) -> float:
    return min(1.0, (n_sources - 1) / 2)  # 1 source = 0, 3+ sources = 1


def recency_component(first_seen: datetime, now: datetime) -> float:
    hours = (now - first_seen).total_seconds() / 3600
    return math.exp(-hours / 24)


def score(
    *,
    rank: int | None,
    snapshots: list[tuple[datetime, float | None]],
    metric: float | None,
    engagement: float | None,
    n_sources: int,
    first_seen: datetime,
    now: datetime,
) -> tuple[float, dict]:
    parts = {
        "rank": rank_component(rank),
        "velocity": velocity_component(snapshots),
        "engagement": engagement_component(metric, engagement),
        "cross_source": cross_source_component(n_sources),
        "recency": recency_component(first_seen, now),
    }
    total = 100 * sum(WEIGHTS[k] * v for k, v in parts.items())
    return round(total, 1), {k: round(v, 3) for k, v in parts.items()}
