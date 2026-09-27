"""SPEC 6 stage 2: eight weighted signals -> score 0-100 and band. Pure.

    weights = resolve_weights(profile.scoring_weights)      # defaults + validated overrides
    signals = rule_signals(profile, opp)                     # code, value, geography, buyer
    signals = signals.with_precomputed(semantic=0.8, keyword=0.4, past_performance=0.7,
                                       eligibility=0.5)      # M4-03 / M4-04 fill these
    result = weighted_score(signals, weights, cap=30)        # cap from stage 1
    result.score, result.band, result.breakdown
    # breakdown = {signal: {raw, weight, weighted, note?, detail?}}

Signal values are 0..1. A signal nobody computed yet is 0.5 with the note "not computed"
(SPEC 6: missing data = 0.5). Bands: high >= 70, medium 50-69, low < 50; the set-aside cap
is applied after weighting and before banding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.core.matching.types import MatchOpportunity, MatchProfile
from app.core.normalize.buyer import normalized_buyer
from app.core.preferences import DEFAULT_SCORING_WEIGHTS, validate_scoring_weights

SIGNAL_NAMES: tuple[str, ...] = tuple(DEFAULT_SCORING_WEIGHTS)
HIGH_MIN = Decimal(70)
MEDIUM_MIN = Decimal(50)
NOT_COMPUTED = "not computed"
UNKNOWN = Decimal("0.5")
_ONE = Decimal(1)
_ZERO = Decimal(0)
_CENT = Decimal("0.01")


def _dec(value: Decimal | float | int) -> Decimal:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if number < 0 or number > 1:
        raise ValueError(f"signal value must be within 0..1, got {value}")
    return number


@dataclass(frozen=True, slots=True)
class SignalValue:
    raw: Decimal  # 0..1
    note: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, raw: Decimal | float | int, note: str | None = None, **detail: Any) -> SignalValue:
        return cls(_dec(raw), note, dict(detail))

    @classmethod
    def unknown(cls, note: str = NOT_COMPUTED) -> SignalValue:
        return cls(UNKNOWN, note)


@dataclass(frozen=True, slots=True)
class Signals:
    """One value per SPEC 6 signal; None = not computed (scored as 0.5)."""

    code_match: SignalValue | None = None
    semantic_similarity: SignalValue | None = None
    keyword_match: SignalValue | None = None
    eligibility: SignalValue | None = None
    value_fit: SignalValue | None = None
    geography: SignalValue | None = None
    buyer_affinity: SignalValue | None = None
    past_performance_relevance: SignalValue | None = None

    def get(self, name: str) -> SignalValue:
        if name not in SIGNAL_NAMES:
            raise KeyError(name)
        value: SignalValue | None = getattr(self, name)
        return value if value is not None else SignalValue.unknown()

    def with_precomputed(
        self,
        *,
        semantic: Decimal | float | None = None,
        keyword: Decimal | float | None = None,
        past_performance: Decimal | float | None = None,
        eligibility: SignalValue | Decimal | float | None = None,
    ) -> Signals:
        """Attach the signals computed elsewhere (embeddings, BM25, eligibility rules)."""
        updates: dict[str, SignalValue] = {}
        if semantic is not None:
            updates["semantic_similarity"] = SignalValue.of(semantic, "max cosine vs service lines")
        if keyword is not None:
            updates["keyword_match"] = SignalValue.of(keyword, "weighted include keywords")
        if past_performance is not None:
            updates["past_performance_relevance"] = SignalValue.of(
                past_performance, "best cosine vs past performance"
            )
        if eligibility is not None:
            updates["eligibility"] = (
                eligibility if isinstance(eligibility, SignalValue) else SignalValue.of(eligibility)
            )
        return replace(self, **updates)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: Decimal  # 0-100, two decimals, after the cap
    band: str  # high | medium | low
    breakdown: dict[str, dict[str, Any]]
    uncapped: Decimal
    cap: int | None = None

    @property
    def capped(self) -> bool:
        return self.cap is not None and self.uncapped > Decimal(self.cap)


# --- weights -------------------------------------------------------------------------------


def resolve_weights(overrides: Mapping[str, object] | None) -> dict[str, int]:
    """SPEC 6 defaults, or the profile's validated override (exact keys, sum 100)."""
    if not overrides:
        return dict(DEFAULT_SCORING_WEIGHTS)
    return validate_scoring_weights(overrides)


def band_for(score: Decimal | int | float) -> str:
    value = Decimal(str(score))
    if value >= HIGH_MIN:
        return "high"
    if value >= MEDIUM_MIN:
        return "medium"
    return "low"


def weighted_score(
    signals: Signals, weights: Mapping[str, int], *, cap: int | None = None
) -> ScoreResult:
    resolved = resolve_weights(weights)
    breakdown: dict[str, dict[str, Any]] = {}
    total = _ZERO
    for name in SIGNAL_NAMES:
        value = signals.get(name)
        weight = resolved[name]
        weighted = (value.raw * weight).quantize(_CENT, rounding=ROUND_HALF_UP)
        total += weighted
        entry: dict[str, Any] = {
            "raw": float(value.raw),
            "weight": weight,
            "weighted": float(weighted),
        }
        if value.note:
            entry["note"] = value.note
        if value.detail:
            entry["detail"] = value.detail
        breakdown[name] = entry
    uncapped = min(total, Decimal(100)).quantize(_CENT)
    score = uncapped if cap is None else min(uncapped, Decimal(cap).quantize(_CENT))
    return ScoreResult(
        score=score, band=band_for(score), breakdown=breakdown, uncapped=uncapped, cap=cap
    )


# --- rule-based signals ----------------------------------------------------------------------


def _upper(values: tuple[str, ...]) -> set[str]:
    return {v.strip().upper() for v in values if v and v.strip()}


def code_match(profile: MatchProfile, opp: MatchOpportunity) -> SignalValue:
    """Exact NAICS/PSC/ALN/GeM category = 1.0; same 4-digit NAICS = 0.6; else 0. A notice
    without any code cannot be compared -> 0.5 (unknown)."""
    pairs = (
        ("naics", _upper(opp.naics), _upper(profile.codes_for("naics"))),
        ("psc", _upper(opp.psc), _upper(profile.codes_for("psc"))),
        ("aln", _upper(opp.aln), _upper(profile.codes_for("aln"))),
        (
            "india_category",
            _upper(opp.india_category),
            _upper(profile.codes_for("gem")) | _upper(profile.codes_for("india_category")),
        ),
    )
    if not any(opp_codes for _, opp_codes, _ in pairs):
        return SignalValue.unknown("notice lists no codes")
    for scheme, opp_codes, mine in pairs:
        hit = sorted(opp_codes & mine)
        if hit:
            return SignalValue.of(1, f"exact {scheme} match", scheme=scheme, codes=hit)
    opp_naics = {c[:4] for c in _upper(opp.naics) if len(c) >= 4}
    mine_naics = {c[:4] for c in _upper(profile.codes_for("naics")) if len(c) >= 4}
    near = sorted(opp_naics & mine_naics)
    if near:
        return SignalValue.of(Decimal("0.6"), "same 4-digit NAICS", scheme="naics", codes=near)
    return SignalValue.of(0, "no code in common")


def _profile_range(
    profile: MatchProfile, opp: MatchOpportunity
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, str]:
    """(lo, hi, opp_lo, opp_hi, currency) in one currency: INR when the profile has an INR
    range and the notice is in INR, else USD."""
    use_inr = opp.currency.upper() == "INR" and (
        profile.value_min_inr is not None or profile.value_max_inr is not None
    )
    if use_inr:
        return (
            profile.value_min_inr,
            profile.value_max_inr,
            opp.estimated_value_min,
            opp.estimated_value_max,
            "INR",
        )
    return (
        profile.value_min_usd,
        profile.value_max_usd,
        opp.estimated_value_min_usd,
        opp.estimated_value_max_usd,
        "USD",
    )


def value_fit(profile: MatchProfile, opp: MatchOpportunity) -> SignalValue:
    """Inside the profile's value range = 1, within 2x of it = 0.5, else 0; unknown value or
    no range on the profile = 0.5."""
    lo, hi, opp_lo, opp_hi, currency = _profile_range(profile, opp)
    if lo is None and hi is None:
        return SignalValue.unknown("no value range on profile")
    opp_lo = opp_lo if opp_lo is not None else opp_hi
    opp_hi = opp_hi if opp_hi is not None else opp_lo
    if opp_lo is None or opp_hi is None:
        return SignalValue.unknown("notice value unknown")
    detail = {"currency": currency, "value": [str(opp_lo), str(opp_hi)]}

    def within(low: Decimal | None, high: Decimal | None) -> bool:
        return (low is None or opp_hi >= low) and (high is None or opp_lo <= high)

    if within(lo, hi):
        return SignalValue.of(1, "inside value range", **detail)
    if within(None if lo is None else lo / 2, None if hi is None else hi * 2):
        return SignalValue.of(Decimal("0.5"), "within 2x of value range", **detail)
    return SignalValue.of(0, "outside value range", **detail)


def geography(profile: MatchProfile, opp: MatchOpportunity) -> SignalValue:
    """Place of performance in the target states/cities or remote = 1; a profile without
    state/city targets covers the whole (already filtered) country = 1; unknown place = 0.5."""
    place = opp.place_of_performance or {}
    if place.get("remote"):
        return SignalValue.of(1, "remote work")
    states = _upper(profile.target_us_states if opp.region == "us" else profile.target_in_states)
    cities = {c.strip().lower() for c in profile.target_cities if c.strip()}
    if not states and not cities:
        return SignalValue.of(1, "no state or city targets: whole country")
    state = str(place.get("state") or "").strip().upper()
    city = str(place.get("city") or "").strip().lower()
    if state and state in states:
        return SignalValue.of(1, "place of performance in target states", state=state)
    if city and city in cities:
        return SignalValue.of(1, "place of performance in target cities", city=city)
    if not state and not city:
        return SignalValue.unknown("place of performance unknown")
    return SignalValue.of(0, "place of performance outside targets", state=state or None)


def _norm_names(names: tuple[str, ...]) -> list[str]:
    return [n for n in (normalized_buyer(name) for name in names) if n]


def buyer_affinity(profile: MatchProfile, opp: MatchOpportunity) -> SignalValue:
    """Past customer (past_performance) or target buyer = 1, else 0 (blocked buyers never
    reach stage 2). Names compare after core.normalize.buyer normalisation, whole-word."""
    buyers = _norm_names(opp.buyers)
    if not buyers:
        return SignalValue.unknown("buyer unknown")
    for label, names in (
        ("past customer", _norm_names(profile.past_customers)),
        ("target buyer", _norm_names(profile.target_buyers)),
    ):
        for entry in names:
            for buyer in buyers:
                if buyer == entry or f" {entry} " in f" {buyer} " or f" {buyer} " in f" {entry} ":
                    return SignalValue.of(1, label, buyer=buyer)
    return SignalValue.of(0, "buyer not a past customer or target")


def rule_signals(profile: MatchProfile, opp: MatchOpportunity) -> Signals:
    """The four signals that need only the profile and the notice."""
    return Signals(
        code_match=code_match(profile, opp),
        value_fit=value_fit(profile, opp),
        geography=geography(profile, opp),
        buyer_affinity=buyer_affinity(profile, opp),
    )
