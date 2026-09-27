"""Stages 1 + 2 in one call (SPEC 6). Pure.

    outcome = evaluate(profile, opp, now, semantic=0.8, keyword=0.3, past_performance=0.6)
    outcome.band          # filtered | high | medium | low
    outcome.score         # Decimal 0-100 (0 when filtered)
    outcome.breakdown     # jsonb-ready: {"filters": {...}, "signals": {...}, "label": ...}
    outcome.filtered_reason, outcome.ineligible_set_aside

The embedding / keyword signals arrive precomputed (M4-03) or default to 0.5; the
eligibility signal is computed here from the notice's extracted criteria (M4-04) unless a
caller passes one in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.matching.eligibility_signal import eligibility_signal
from app.core.matching.filters import FilterResult, hard_filters
from app.core.matching.score import (
    ScoreResult,
    Signals,
    SignalValue,
    resolve_weights,
    rule_signals,
    weighted_score,
)
from app.core.matching.types import MatchOpportunity, MatchProfile

FILTERED = "filtered"


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    filters: FilterResult
    signals: Signals | None
    result: ScoreResult | None

    @property
    def band(self) -> str:
        return FILTERED if self.result is None else self.result.band

    @property
    def score(self) -> Decimal:
        return Decimal("0.00") if self.result is None else self.result.score

    @property
    def filtered_reason(self) -> str | None:
        return None if self.filters.keep else self.filters.reason

    @property
    def ineligible_set_aside(self) -> bool:
        return self.filters.ineligible_set_aside

    @property
    def breakdown(self) -> dict[str, Any]:
        out: dict[str, Any] = {"filters": self.filters.as_dict()}
        if self.result is not None:
            out["signals"] = self.result.breakdown
            out["uncapped_score"] = float(self.result.uncapped)
            eligibility = self.result.breakdown.get("eligibility", {}).get("detail", {})
            if "criteria" in eligibility:
                out["eligibility"] = eligibility["criteria"]
        if self.filters.label:
            out["label"] = self.filters.label
            out["cap"] = self.filters.cap
        return out


def evaluate(
    profile: MatchProfile,
    opp: MatchOpportunity,
    now: datetime,
    *,
    semantic: SignalValue | Decimal | float | None = None,
    keyword: SignalValue | Decimal | float | None = None,
    past_performance: SignalValue | Decimal | float | None = None,
    eligibility: SignalValue | Decimal | float | None = None,
) -> MatchOutcome:
    filters = hard_filters(profile, opp, now)
    if not filters.keep:
        return MatchOutcome(filters, None, None)
    signals = rule_signals(profile, opp).with_precomputed(
        semantic=semantic,
        keyword=keyword,
        past_performance=past_performance,
        eligibility=eligibility
        if eligibility is not None
        else eligibility_signal(profile, opp, now.date()),
    )
    result = weighted_score(signals, resolve_weights(profile.scoring_weights), cap=filters.cap)
    return MatchOutcome(filters, signals, result)
