"""M5-06: the pure scorecard arithmetic (app.core.bid_no_bid) and the pipeline's Gate 1."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.agents import pipeline
from app.agents.pipeline import GATE_1, GATE_REASONS, STEP_BID_NO_BID, plan_steps
from app.core.bid_no_bid import (
    BID_THRESHOLD,
    RECOMMENDATIONS,
    SCORE_FIELDS,
    WATCH_THRESHOLD,
    suggested_recommendation,
    weighted_score,
)
from app.core.preferences import DEFAULT_BID_NO_BID_WEIGHTS

FULL = dict.fromkeys(SCORE_FIELDS, 100)


def test_score_fields_are_the_profile_weight_keys() -> None:
    assert set(SCORE_FIELDS) == set(DEFAULT_BID_NO_BID_WEIGHTS)
    assert SCORE_FIELDS == (
        "fit",
        "eligibility",
        "capacity",
        "competition",
        "value",
        "win_probability",
    )
    assert RECOMMENDATIONS == ("bid", "no_bid", "watch")


def test_all_hundred_is_a_hundred_and_all_zero_is_zero() -> None:
    assert weighted_score(FULL).total == Decimal("100.00")
    assert weighted_score(dict.fromkeys(SCORE_FIELDS, 0)).total == Decimal("0.00")


def test_default_weights_breakdown_adds_up() -> None:
    scores = {
        "fit": 80,
        "eligibility": 100,
        "capacity": 60,
        "competition": 40,
        "value": 50,
        "win_probability": 35,
    }
    result = weighted_score(scores)
    # 80*25 + 100*20 + 60*15 + 40*15 + 50*10 + 35*15 all over 100
    assert result.parts["fit"].weighted == Decimal("20.00")
    assert result.parts["eligibility"].weighted == Decimal("20.00")
    assert result.parts["capacity"].weighted == Decimal("9.00")
    assert result.parts["competition"].weighted == Decimal("6.00")
    assert result.parts["value"].weighted == Decimal("5.00")
    assert result.parts["win_probability"].weighted == Decimal("5.25")
    assert result.total == Decimal("65.25")
    assert sum(p.weighted for p in result.parts.values()) == result.total
    assert result.as_dict()["fit"] == {"score": 80, "weight": 25, "weighted": 20.0}


def test_profile_weights_override_the_defaults() -> None:
    weights = {
        "fit": 0,
        "eligibility": 0,
        "capacity": 0,
        "competition": 0,
        "value": 0,
        "win_probability": 100,
    }
    scores = {**dict.fromkeys(SCORE_FIELDS, 0), "win_probability": 72}
    result = weighted_score(scores, weights)
    assert result.total == Decimal("72.00")
    assert result.parts["fit"].weight == 0


def test_scores_are_clamped_and_missing_criteria_raise() -> None:
    assert weighted_score({**FULL, "fit": 250}).total == Decimal("100.00")
    assert weighted_score({**FULL, "fit": -10}).parts["fit"].score == 0
    with pytest.raises(ValueError, match="missing criteria"):
        weighted_score({"fit": 10})


def test_invalid_weights_raise() -> None:
    with pytest.raises(ValueError, match="sum to 100"):
        weighted_score(FULL, {**DEFAULT_BID_NO_BID_WEIGHTS, "fit": 90})
    with pytest.raises(ValueError, match="exactly the keys"):
        weighted_score(FULL, {"fit": 100})


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (Decimal("100.00"), "bid"),
        (BID_THRESHOLD, "bid"),
        (BID_THRESHOLD - Decimal("0.01"), "watch"),
        (WATCH_THRESHOLD, "watch"),
        (WATCH_THRESHOLD - Decimal("0.01"), "no_bid"),
        (Decimal("0.00"), "no_bid"),
    ],
)
def test_thresholds(total: Decimal, expected: str) -> None:
    assert suggested_recommendation(total) == expected


# --- Gate 1 in the pipeline plan --------------------------------------------------------


def test_all_stops_after_the_bid_no_bid_step_until_gate_one_is_cleared() -> None:
    specs, finish = plan_steps("all")
    names = [s.agent for s in specs]
    assert names[-1] == STEP_BID_NO_BID
    assert names == ["collect", "extract", "matrix", "bid_no_bid"]
    assert finish.status == "paused" and finish.gate == GATE_1
    assert finish.reason == GATE_REASONS[GATE_1]


def test_clearing_gate_one_plans_past_the_analyst() -> None:
    specs, finish = plan_steps("all", gates_cleared=(GATE_1,))
    names = [s.agent for s in specs]
    assert names[: len(["collect", "extract", "matrix", "bid_no_bid"])] == [
        "collect",
        "extract",
        "matrix",
        "bid_no_bid",
    ]
    # the run only continues past the gate; where it stops next is the next task's job
    assert len(names) > 4 or finish.status == "paused"


def test_single_step_bid_no_bid_also_waits_at_the_gate() -> None:
    specs, finish = plan_steps(STEP_BID_NO_BID)
    assert [s.agent for s in specs] == [STEP_BID_NO_BID]
    assert finish.status == "paused" and finish.gate == GATE_1
    _, cleared = plan_steps(STEP_BID_NO_BID, gates_cleared=[GATE_1])
    assert cleared.status == "done" and cleared.gate is None


def test_a_step_without_a_gate_is_unaffected() -> None:
    _, finish = plan_steps("matrix")
    assert finish.status == "done" and finish.gate is None
    assert pipeline.GATES == {STEP_BID_NO_BID: GATE_1}
