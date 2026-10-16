"""Popularity score: pure functions. Percentile-normalised components, decay, corroboration."""

from __future__ import annotations

import math
from datetime import UTC, datetime

HALF_LIFE_DAYS = 14.0


def components(stars: float | None, delta7: float | None, hn_points: float | None,
               hn_comments: float | None, mention_domains: float | None) -> dict[str, float]:
    s = stars or 0.0
    return {
        "pop": math.log1p(s),
        # sqrt damps mega-repos so a 3k-star project gaining 500 beats a 100k one gaining 500.
        "vel": (delta7 / math.sqrt(max(s, 50.0))) if delta7 is not None and delta7 > 0 else 0.0,
        "hn": math.log1p(hn_points or 0) + 0.5 * math.log1p(hn_comments or 0),
        "mentions": math.log1p(mention_domains or 0),
    }


def percentiles(values: list[float]) -> list[float]:
    """Rank-based 0..1; ties share the average rank. All-zero columns stay 0."""
    n = len(values)
    if n == 0:
        return []
    out = []
    for v in values:
        if v <= 0:
            out.append(0.0)
            continue
        below = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append((below + (equal - 1) / 2) / max(n - 1, 1) if n > 1 else 1.0)
    return out


def decay(last_seen_iso: str | None, now: datetime | None = None) -> float:
    if not last_seen_iso:
        return 1.0
    now = now or datetime.now(UTC)
    age = max((now - datetime.fromisoformat(last_seen_iso)).total_seconds() / 86400, 0)
    return math.exp(-math.log(2) * age / HALF_LIFE_DAYS)


def corroboration(n_sources: int) -> float:
    return 1 + 0.15 * max(n_sources - 1, 0)


def score_pool(rows: list[dict], weights: dict[str, float], now: datetime | None = None) -> list[dict]:
    """rows: {id, comps, last_seen, n_sources}. Returns [{id, score, detail}] with score on 0..~130."""
    cols = {k: percentiles([r["comps"][k] for r in rows]) for k in weights}
    out = []
    for i, r in enumerate(rows):
        base = sum(weights[k] * cols[k][i] for k in weights)
        mult = decay(r.get("last_seen"), now) * corroboration(r.get("n_sources", 1))
        out.append(
            {
                "id": r["id"],
                "score": round(100 * base * mult, 2),
                "detail": {k: round(cols[k][i], 3) for k in weights} | {"mult": round(mult, 3)},
            }
        )
    return out
