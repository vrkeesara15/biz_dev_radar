"""M4-02: SPEC 6 stage-2 weighted score: eight signals, per-profile weights, bands, breakdown."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.core.matching.engine import MatchOutcome, evaluate
from app.core.matching.score import (
    NOT_COMPUTED,
    SIGNAL_NAMES,
    ScoreResult,
    Signals,
    SignalValue,
    band_for,
    buyer_affinity,
    code_match,
    geography,
    resolve_weights,
    rule_signals,
    value_fit,
    weighted_score,
)
from app.core.matching.types import MatchOpportunity, MatchProfile
from app.core.preferences import DEFAULT_SCORING_WEIGHTS

D = Decimal
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

PROFILE = MatchProfile(
    region="us",
    target_countries=("US",),
    target_us_states=("VA", "MD"),
    target_cities=("Austin",),
    remote_ok=True,
    target_buyers=("General Services Administration",),
    past_customers=("Internal Revenue Service",),
    value_min_usd=D("100000"),
    value_max_usd=D("1000000"),
    codes={"naics": ("541511", "541512"), "psc": ("D302",), "aln": ("93.778",)},
)

OPP = MatchOpportunity(
    region="us",
    country="US",
    notice_type="rfp",
    title="Cloud migration",
    buyer_org="Department of the Treasury",
    buyer_sub_org="Internal Revenue Service",
    naics=("541511",),
    place_of_performance={"state": "VA", "country": "US"},
    estimated_value_min_usd=D("200000"),
    estimated_value_max_usd=D("400000"),
    response_due_at=NOW + timedelta(days=10),
)


def _full(raw: float) -> Signals:
    value = SignalValue.of(raw)
    return Signals(**{name: value for name in SIGNAL_NAMES})


# --- weights ------------------------------------------------------------------------------


def test_default_weights_are_spec_6_and_sum_100() -> None:
    assert SIGNAL_NAMES == (
        "code_match",
        "semantic_similarity",
        "keyword_match",
        "eligibility",
        "value_fit",
        "geography",
        "buyer_affinity",
        "past_performance_relevance",
    )
    assert [DEFAULT_SCORING_WEIGHTS[n] for n in SIGNAL_NAMES] == [25, 25, 10, 15, 5, 5, 5, 10]
    assert resolve_weights(None) == DEFAULT_SCORING_WEIGHTS
    assert resolve_weights({}) == DEFAULT_SCORING_WEIGHTS
    assert resolve_weights(None) is not DEFAULT_SCORING_WEIGHTS  # a copy


def test_profile_override_is_validated() -> None:
    custom = {**DEFAULT_SCORING_WEIGHTS, "code_match": 35, "semantic_similarity": 15}
    assert resolve_weights(custom)["code_match"] == 35
    with pytest.raises(ValueError, match="sum to 100"):
        resolve_weights({**DEFAULT_SCORING_WEIGHTS, "code_match": 30})
    with pytest.raises(ValueError, match="exactly the keys"):
        resolve_weights({"code_match": 100})


# --- weighted score, bands, breakdown -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "score", "band"),
    [(1.0, "100.00", "high"), (0.7, "70.00", "high"), (0.5, "50.00", "medium"),
     (0.69, "69.00", "medium"), (0.49, "49.00", "low"), (0.0, "0.00", "low")],
)  # fmt: skip
def test_bands(raw: float, score: str, band: str) -> None:
    result = weighted_score(_full(raw), DEFAULT_SCORING_WEIGHTS)
    assert result.score == D(score)
    assert result.band == band
    assert result.capped is False and result.cap is None


@pytest.mark.parametrize(
    ("value", "band"), [(70, "high"), (69.99, "medium"), (50, "medium"), (49.99, "low"), (0, "low")]
)
def test_band_for(value: float, band: str) -> None:
    assert band_for(value) == band


def test_breakdown_lists_raw_weight_weighted_per_signal_and_uses_profile_weights() -> None:
    weights = {**DEFAULT_SCORING_WEIGHTS, "code_match": 30, "semantic_similarity": 20}
    signals = Signals(
        code_match=SignalValue.of(1, "exact naics match", scheme="naics", codes=["541511"]),
        semantic_similarity=SignalValue.of(D("0.8")),
        keyword_match=SignalValue.of(0.4),
        eligibility=SignalValue.of(D("0.5"), "unknown"),
        value_fit=SignalValue.of(1),
        geography=SignalValue.of(0),
        buyer_affinity=SignalValue.of(1),
        past_performance_relevance=SignalValue.of(D("0.25")),
    )
    result = weighted_score(signals, weights)
    # 30 + 16 + 4 + 7.5 + 5 + 0 + 5 + 2.5 = 70
    assert result.score == D("70.00") and result.band == "high"
    assert set(result.breakdown) == set(SIGNAL_NAMES)
    assert result.breakdown["code_match"] == {
        "raw": 1.0,
        "weight": 30,
        "weighted": 30.0,
        "note": "exact naics match",
        "detail": {"scheme": "naics", "codes": ["541511"]},
    }
    assert result.breakdown["semantic_similarity"] == {"raw": 0.8, "weight": 20, "weighted": 16.0}
    assert result.breakdown["eligibility"]["weighted"] == 7.5
    assert result.breakdown["past_performance_relevance"]["weighted"] == 2.5
    assert sum(e["weighted"] for e in result.breakdown.values()) == 70.0
    assert sum(e["weight"] for e in result.breakdown.values()) == 100


def test_not_computed_signals_score_half_with_a_note() -> None:
    signals = Signals(code_match=SignalValue.of(1))
    result = weighted_score(signals, DEFAULT_SCORING_WEIGHTS)
    # 25 + 0.5 * 75 = 62.5
    assert result.score == D("62.50") and result.band == "medium"
    for name in SIGNAL_NAMES[1:]:
        assert result.breakdown[name] == {
            "raw": 0.5,
            "weight": _w(name),
            "weighted": _w(name) / 2,
            "note": NOT_COMPUTED,
        }
    assert "note" not in result.breakdown["code_match"]


def _w(name: str) -> int:
    return DEFAULT_SCORING_WEIGHTS[name]


def test_cap_applies_after_weighting_and_changes_the_band() -> None:
    result = weighted_score(_full(0.9), DEFAULT_SCORING_WEIGHTS, cap=30)
    assert result.uncapped == D("90.00")
    assert result.score == D("30.00") and result.band == "low"
    assert result.capped is True and result.cap == 30
    below = weighted_score(_full(0.2), DEFAULT_SCORING_WEIGHTS, cap=30)
    assert below.score == D("20.00") and below.capped is False and below.cap == 30


def test_with_precomputed_fills_the_external_signals() -> None:
    base = Signals(code_match=SignalValue.of(1))
    filled = base.with_precomputed(
        semantic=0.9, keyword=D("0.3"), past_performance=0.6, eligibility=0.5
    )
    assert filled.semantic_similarity is not None and filled.semantic_similarity.raw == D("0.9")
    assert filled.keyword_match is not None and filled.keyword_match.raw == D("0.3")
    assert filled.past_performance_relevance is not None
    assert filled.past_performance_relevance.note == "best cosine vs past performance"
    assert filled.eligibility is not None and filled.eligibility.raw == D("0.5")
    assert filled.code_match is base.code_match  # untouched
    custom = SignalValue.of(0.25, "3 criteria", criteria=[{"name": "turnover"}])
    assert base.with_precomputed(eligibility=custom).eligibility is custom
    assert base.with_precomputed().get("semantic_similarity").note == NOT_COMPUTED
    with pytest.raises(KeyError):
        base.get("nope")


def test_signal_values_must_be_within_unit_interval() -> None:
    with pytest.raises(ValueError, match=r"0\.\.1"):
        SignalValue.of(1.2)
    with pytest.raises(ValueError, match=r"0\.\.1"):
        SignalValue.of(-0.1)
    assert SignalValue.of(D("0.75")).raw == D("0.75")
    assert SignalValue.of(1).raw == D(1)


def test_score_never_exceeds_100_on_rounding() -> None:
    signals = Signals(**{name: SignalValue.of(D("0.99999")) for name in SIGNAL_NAMES})
    result = weighted_score(signals, DEFAULT_SCORING_WEIGHTS)
    assert result.score <= D(100)
    assert isinstance(result, ScoreResult)


# --- code match -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile_codes", "opp_kwargs", "raw", "note"),
    [
        ({"naics": ("541511",)}, {"naics": ("541511",)}, "1", "exact naics match"),
        ({"naics": ("541511",)}, {"naics": ("541519", "541511")}, "1", "exact naics match"),
        ({"psc": ("D302",)}, {"naics": ("541511",), "psc": ("d302",)}, "1", "exact psc match"),
        ({"aln": ("93.778",)}, {"aln": ("93.778",)}, "1", "exact aln match"),
        ({"gem": ("Laptop - Notebook",)}, {"india_category": ("laptop - notebook",)}, "1",
         "exact india_category match"),
        ({"india_category": ("IT Services",)}, {"india_category": ("IT Services",)}, "1",
         "exact india_category match"),
        # same 4-digit NAICS
        ({"naics": ("541511",)}, {"naics": ("541519",)}, "0.6", "same 4-digit NAICS"),
        ({"naics": ("541511",), "psc": ("D302",)}, {"naics": ("541512",), "psc": ("R499",)},
         "0.6", "same 4-digit NAICS"),
        # no overlap at all
        ({"naics": ("541511",)}, {"naics": ("236220",)}, "0", "no code in common"),
        ({"psc": ("D302",)}, {"naics": ("236220",)}, "0", "no code in common"),
        # the notice lists no codes: unknown
        ({"naics": ("541511",)}, {}, "0.5", "notice lists no codes"),
        # the profile lists none: it cannot match
        ({}, {"naics": ("541511",)}, "0", "no code in common"),
        # 3-digit fragments never count as a 4-digit match
        ({"naics": ("541",)}, {"naics": ("541511",)}, "0", "no code in common"),
    ],
)  # fmt: skip
def test_code_match(
    profile_codes: dict[str, tuple[str, ...]], opp_kwargs: dict[str, Any], raw: str, note: str
) -> None:
    profile = replace(PROFILE, codes=profile_codes)
    blank: dict[str, Any] = {"naics": (), "psc": (), "aln": (), "india_category": ()}
    value = code_match(profile, replace(OPP, **{**blank, **opp_kwargs}))
    assert value.raw == D(raw)
    assert value.note == note


def test_code_match_detail_names_the_codes() -> None:
    value = code_match(PROFILE, replace(OPP, naics=("541512", "541511")))
    assert value.detail == {"scheme": "naics", "codes": ["541511", "541512"]}
    near = code_match(PROFILE, replace(OPP, naics=("541519",)))
    assert near.detail == {"scheme": "naics", "codes": ["5415"]}


# --- value fit --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lo", "hi", "vmin", "vmax", "raw", "note"),
    [
        ("100000", "1000000", "200000", "400000", "1", "inside value range"),
        ("100000", "1000000", "50000", "150000", "1", "inside value range"),  # overlaps
        ("100000", "1000000", "1000000", None, "1", "inside value range"),  # boundary
        ("100000", "1000000", "1500000", "1800000", "0.5", "within 2x of value range"),
        ("100000", "1000000", "60000", "60000", "0.5", "within 2x of value range"),
        ("100000", "1000000", "2000001", "3000000", "0", "outside value range"),
        ("100000", "1000000", "1000", "49999", "0", "outside value range"),
        # open-ended profile ranges
        (None, "1000000", "5", "10", "1", "inside value range"),
        ("100000", None, "99999999", None, "1", "inside value range"),
        ("100000", None, "40000", None, "0", "outside value range"),
        # unknowns
        ("100000", "1000000", None, None, "0.5", "notice value unknown"),
        (None, None, "200000", "400000", "0.5", "no value range on profile"),
    ],
)
def test_value_fit_usd(
    lo: str | None, hi: str | None, vmin: str | None, vmax: str | None, raw: str, note: str
) -> None:
    profile = replace(
        PROFILE,
        value_min_usd=None if lo is None else D(lo),
        value_max_usd=None if hi is None else D(hi),
    )
    opp = replace(
        OPP,
        estimated_value_min_usd=None if vmin is None else D(vmin),
        estimated_value_max_usd=None if vmax is None else D(vmax),
    )
    value = value_fit(profile, opp)
    assert value.raw == D(raw)
    assert value.note == note
    if raw != "0.5":
        assert value.detail["currency"] == "USD"


def test_value_fit_uses_inr_range_for_inr_notices() -> None:
    profile = MatchProfile(
        region="in", value_min_inr=D("1000000"), value_max_inr=D("50000000"), value_min_usd=D(1)
    )
    inr = MatchOpportunity(
        region="in",
        country="IN",
        notice_type="gem_bid",
        title="x",
        currency="INR",
        estimated_value_min=D("2000000"),
        estimated_value_max=D("3000000"),
        estimated_value_min_usd=D("24000"),
        estimated_value_max_usd=D("36000"),
    )
    value = value_fit(profile, inr)
    assert value.raw == D(1) and value.detail["currency"] == "INR"
    assert value.detail["value"] == ["2000000", "3000000"]
    # an IN profile with only a USD range compares the USD copies
    usd_only = replace(profile, value_min_inr=None, value_max_inr=None, value_max_usd=D("30000"))
    assert value_fit(usd_only, inr).detail["currency"] == "USD"


# --- geography -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile_kwargs", "place", "raw", "note"),
    [
        ({}, {"state": "VA"}, "1", "place of performance in target states"),
        ({}, {"state": "va"}, "1", "place of performance in target states"),
        ({}, {"state": "TX", "city": "Austin"}, "1", "place of performance in target cities"),
        ({}, {"state": "TX", "city": "Dallas"}, "0", "place of performance outside targets"),
        ({}, {"city": "dallas"}, "0", "place of performance outside targets"),
        ({}, {"remote": True}, "1", "remote work"),
        ({}, {"state": "TX", "remote": True}, "1", "remote work"),
        ({}, {}, "0.5", "place of performance unknown"),
        ({}, None, "0.5", "place of performance unknown"),
        ({}, {"country": "US"}, "0.5", "place of performance unknown"),
        ({"target_us_states": (), "target_cities": ()}, {"state": "TX"}, "1",
         "no state or city targets: whole country"),
        ({"target_us_states": (), "target_cities": ()}, None, "1",
         "no state or city targets: whole country"),
    ],
)  # fmt: skip
def test_geography_us(
    profile_kwargs: dict[str, Any], place: dict[str, Any] | None, raw: str, note: str
) -> None:
    value = geography(replace(PROFILE, **profile_kwargs), replace(OPP, place_of_performance=place))
    assert value.raw == D(raw)
    assert value.note == note


def test_geography_in_uses_indian_states() -> None:
    profile = MatchProfile(region="in", target_in_states=("TS", "KA"), target_us_states=("VA",))
    opp = MatchOpportunity(region="in", country="IN", notice_type="gem_bid", title="x")
    assert geography(profile, replace(opp, place_of_performance={"state": "TS"})).raw == D(1)
    assert geography(profile, replace(opp, place_of_performance={"state": "MH"})).raw == D(0)
    # a US notice never matches through the Indian list
    assert geography(
        replace(profile, region="us"), replace(OPP, place_of_performance={"state": "TS"})
    ).raw == D(0)


# --- buyer affinity --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile_kwargs", "opp_kwargs", "raw", "note"),
    [
        ({}, {"buyer_sub_org": "Internal Revenue Service"}, "1", "past customer"),
        ({}, {"buyer_org": "INTERNAL REVENUE SERVICE, Inc."}, "1", "past customer"),
        ({}, {"buyer_org": "General Services Administration"}, "1", "target buyer"),
        ({}, {"buyer_org": "The General Services Administration"}, "1", "target buyer"),
        # hierarchy levels count, whole words only
        ({}, {"buyer_hierarchy": ("Treasury", "Internal Revenue Service", "Ogden")}, "1",
         "past customer"),
        ({"past_customers": ("Revenue",)}, {"buyer_org": "Internal Revenue Service"}, "1",
         "past customer"),
        ({"past_customers": ("Internal Revenue Service Ogden Campus",)},
         {"buyer_org": "Internal Revenue Service"}, "1", "past customer"),
        ({}, {"buyer_org": "Department of Energy"}, "0", "buyer not a past customer or target"),
        ({"past_customers": (), "target_buyers": ()}, {"buyer_org": "Anyone"}, "0",
         "buyer not a past customer or target"),
        ({}, {}, "0.5", "buyer unknown"),
    ],
)  # fmt: skip
def test_buyer_affinity(
    profile_kwargs: dict[str, Any], opp_kwargs: dict[str, Any], raw: str, note: str
) -> None:
    blank: dict[str, Any] = {"buyer_org": None, "buyer_sub_org": None, "buyer_office": None}
    opp = replace(OPP, **{**blank, **opp_kwargs})
    value = buyer_affinity(replace(PROFILE, **profile_kwargs), opp)
    assert value.raw == D(raw)
    assert value.note == note


def test_rule_signals_computes_the_four_profile_only_signals() -> None:
    signals = rule_signals(PROFILE, OPP)
    assert signals.code_match is not None and signals.code_match.raw == D(1)
    assert signals.value_fit is not None and signals.value_fit.raw == D(1)
    assert signals.geography is not None and signals.geography.raw == D(1)
    assert signals.buyer_affinity is not None and signals.buyer_affinity.raw == D(1)
    for name in (
        "semantic_similarity",
        "keyword_match",
        "eligibility",
        "past_performance_relevance",
    ):
        assert getattr(signals, name) is None


# --- engine: stage 1 + 2 ----------------------------------------------------------------------


def test_evaluate_scores_a_kept_notice_with_defaults_for_missing_signals() -> None:
    outcome = evaluate(PROFILE, OPP, NOW)
    assert isinstance(outcome, MatchOutcome)
    # 25 + 5 + 5 + 5 exact/inside/state/past customer + 0.5 * (25 + 10 + 15 + 10) = 70
    assert outcome.score == D("70.00") and outcome.band == "high"
    assert outcome.filtered_reason is None and outcome.ineligible_set_aside is False
    breakdown = outcome.breakdown
    assert breakdown["filters"]["keep"] is True
    assert breakdown["signals"]["semantic_similarity"]["note"] == NOT_COMPUTED
    assert breakdown["uncapped_score"] == 70.0
    assert "label" not in breakdown and "eligibility" not in breakdown


def test_evaluate_uses_precomputed_signals_and_profile_weights() -> None:
    profile = replace(
        PROFILE,
        scoring_weights={
            **DEFAULT_SCORING_WEIGHTS,
            "code_match": 35,
            "geography": 0,
            "buyer_affinity": 0,
        },
    )
    outcome = evaluate(
        profile, OPP, NOW, semantic=0.2, keyword=0.0, past_performance=0.0, eligibility=0.0
    )
    # 35 + 5 (semantic 0.2*25) + 0 + 0 + 5 value + 0 + 0 + 0 = 45
    assert outcome.score == D("45.00") and outcome.band == "low"
    assert outcome.breakdown["signals"]["code_match"]["weight"] == 35


def test_evaluate_caps_ineligible_set_aside_and_labels_it() -> None:
    outcome = evaluate(
        PROFILE,
        replace(OPP, set_aside="8A"),
        NOW,
        semantic=1,
        keyword=1,
        past_performance=1,
        eligibility=1,
    )
    assert outcome.result is not None and outcome.result.uncapped == D("100.00")
    assert outcome.score == D("30.00") and outcome.band == "low"
    assert outcome.ineligible_set_aside is True
    assert outcome.breakdown["label"] == "Ineligible: set-aside"
    assert outcome.breakdown["cap"] == 30


def test_evaluate_filtered_notice_has_no_score() -> None:
    outcome = evaluate(PROFILE, replace(OPP, response_due_at=NOW - timedelta(days=1)), NOW)
    assert outcome.band == "filtered" and outcome.score == D("0.00")
    assert outcome.filtered_reason == "past_due"
    assert outcome.signals is None and outcome.result is None
    assert outcome.breakdown == {"filters": outcome.filters.as_dict()}


def test_evaluate_exposes_eligibility_criteria_in_breakdown() -> None:
    criteria = [{"name": "turnover", "status": "pass", "reason": "ok"}]
    signal = SignalValue.of(1, "1 criterion", criteria=criteria)
    outcome = evaluate(PROFILE, OPP, NOW, eligibility=signal)
    assert outcome.breakdown["eligibility"] == criteria
    assert outcome.breakdown["signals"]["eligibility"]["detail"]["criteria"] == criteria
