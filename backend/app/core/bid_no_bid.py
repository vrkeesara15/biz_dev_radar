"""Bid/no-bid scorecard arithmetic (SPEC 8 agent 4). Pure: no I/O, no model calls.

The model scores the six criteria 0-100; the profile's `bid_no_bid_weights`
(app.core.preferences, sum 100) turn them into one weighted number, and the thresholds
below turn that number into the recommendation the UI shows next to the model's own.

    breakdown = weighted_score({"fit": 80, ...}, profile.bid_no_bid_weights)
    breakdown.total                      # Decimal, 0-100, two decimals
    suggested_recommendation(breakdown.total)   # bid | watch | no_bid
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.core.preferences import DEFAULT_BID_NO_BID_WEIGHTS, validate_bid_no_bid_weights

# The scorecard criteria, in the order the UI shows them. `value` is the scorecard's
# `value_fit` field (the weight key SPEC/preferences uses is the shorter `value`).
SCORE_FIELDS: tuple[str, ...] = tuple(DEFAULT_BID_NO_BID_WEIGHTS)

RECOMMENDATION_BID = "bid"
RECOMMENDATION_NO_BID = "no_bid"
RECOMMENDATION_WATCH = "watch"
RECOMMENDATIONS: tuple[str, ...] = (
    RECOMMENDATION_BID,
    RECOMMENDATION_NO_BID,
    RECOMMENDATION_WATCH,
)

# A weighted score at or above BID_THRESHOLD suggests bidding; below WATCH_THRESHOLD the
# pursuit is not worth chasing. Between them it is a watch (qualify further, ask the buyer).
BID_THRESHOLD = Decimal(65)
WATCH_THRESHOLD = Decimal(45)

_CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ScorePart:
    score: int
    weight: int
    weighted: Decimal


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    total: Decimal
    parts: dict[str, ScorePart] = field(default_factory=dict)

    def as_dict(self) -> dict[str, dict[str, object]]:
        return {
            name: {"score": p.score, "weight": p.weight, "weighted": float(p.weighted)}
            for name, p in self.parts.items()
        }


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))


def weighted_score(
    scores: Mapping[str, int], weights: Mapping[str, int] | None = None
) -> ScoreBreakdown:
    """Weighted 0-100 total of the six criteria.

    `scores` must carry every criterion (`SCORE_FIELDS`); each is clamped to 0-100.
    `weights` defaults to app.core.preferences.DEFAULT_BID_NO_BID_WEIGHTS and is validated
    (same keys, non-negative, sum 100), so a bad profile override raises here rather than
    silently skewing the recommendation. Per-criterion contributions are rounded to cents
    before summing, so the parts always add up to the total the UI shows.
    """
    validated = validate_bid_no_bid_weights(weights or DEFAULT_BID_NO_BID_WEIGHTS)
    missing = [name for name in SCORE_FIELDS if name not in scores]
    if missing:
        raise ValueError(f"scorecard is missing criteria: {missing}")
    parts: dict[str, ScorePart] = {}
    total = Decimal(0)
    for name in SCORE_FIELDS:
        score = _clamp(scores[name])
        weight = validated[name]
        weighted = (Decimal(score) * Decimal(weight) / Decimal(100)).quantize(
            _CENTS, rounding=ROUND_HALF_UP
        )
        parts[name] = ScorePart(score=score, weight=weight, weighted=weighted)
        total += weighted
    return ScoreBreakdown(total=total.quantize(_CENTS), parts=parts)


def suggested_recommendation(total: Decimal) -> str:
    """The recommendation the weighted score alone implies (the model's own answer is
    kept next to it; a disagreement is worth showing, not hiding)."""
    if total >= BID_THRESHOLD:
        return RECOMMENDATION_BID
    if total >= WATCH_THRESHOLD:
        return RECOMMENDATION_WATCH
    return RECOMMENDATION_NO_BID
