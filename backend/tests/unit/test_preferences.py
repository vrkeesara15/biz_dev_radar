"""M1-06: scoring/bid-no-bid weights, output languages, quiet hours, channel maps."""

import pytest
from app.core.config import Region
from app.core.preferences import (
    DEFAULT_BID_NO_BID_WEIGHTS,
    DEFAULT_CHANNELS_BY_EVENT,
    DEFAULT_MIN_SCORE_DIGEST,
    DEFAULT_MIN_SCORE_INSTANT,
    DEFAULT_SCORING_WEIGHTS,
    NotificationEvent,
    allowed_output_languages,
    in_quiet_hours,
    validate_bid_no_bid_weights,
    validate_channels_by_event,
    validate_min_scores,
    validate_output_languages,
    validate_quiet_hours,
    validate_scoring_weights,
)


def test_default_scoring_weights_are_spec_6() -> None:
    assert DEFAULT_SCORING_WEIGHTS == {
        "code_match": 25,
        "semantic_similarity": 25,
        "keyword_match": 10,
        "eligibility": 15,
        "value_fit": 5,
        "geography": 5,
        "buyer_affinity": 5,
        "past_performance_relevance": 10,
    }
    assert sum(DEFAULT_SCORING_WEIGHTS.values()) == 100
    assert sum(DEFAULT_BID_NO_BID_WEIGHTS.values()) == 100
    assert validate_scoring_weights(DEFAULT_SCORING_WEIGHTS) == DEFAULT_SCORING_WEIGHTS
    assert validate_bid_no_bid_weights(DEFAULT_BID_NO_BID_WEIGHTS) == DEFAULT_BID_NO_BID_WEIGHTS
    assert (DEFAULT_MIN_SCORE_INSTANT, DEFAULT_MIN_SCORE_DIGEST) == (70, 50)


def test_weights_must_sum_to_100_with_exact_keys() -> None:
    edited = {**DEFAULT_SCORING_WEIGHTS, "code_match": 30, "semantic_similarity": 20}
    assert validate_scoring_weights(edited)["code_match"] == 30
    with pytest.raises(ValueError, match="sum to 100, got 105"):
        validate_scoring_weights({**DEFAULT_SCORING_WEIGHTS, "code_match": 30})
    with pytest.raises(ValueError, match="missing=\\['geography'\\]"):
        validate_scoring_weights(
            {k: v for k, v in DEFAULT_SCORING_WEIGHTS.items() if k != "geography"}
        )
    with pytest.raises(ValueError, match="extra=\\['bonus'\\]"):
        validate_scoring_weights({**DEFAULT_SCORING_WEIGHTS, "bonus": 0})
    with pytest.raises(ValueError, match=">= 0"):
        validate_scoring_weights({**DEFAULT_SCORING_WEIGHTS, "code_match": -5, "geography": 35})
    with pytest.raises(ValueError, match="whole number"):
        validate_scoring_weights({**DEFAULT_SCORING_WEIGHTS, "code_match": 24.5, "geography": 5.5})
    with pytest.raises(ValueError, match="whole number"):
        validate_scoring_weights({**DEFAULT_SCORING_WEIGHTS, "code_match": True, "geography": 29})
    assert (
        validate_scoring_weights({**DEFAULT_SCORING_WEIGHTS, "code_match": 25.0})["code_match"]
        == 25
    )
    zeroed = dict.fromkeys(DEFAULT_BID_NO_BID_WEIGHTS, 0) | {"fit": 100}
    assert validate_bid_no_bid_weights(zeroed)["fit"] == 100


def test_output_languages_by_region() -> None:
    assert allowed_output_languages("us") == {"en"}
    assert allowed_output_languages(Region.IN) == {"en", "hi"}
    assert validate_output_languages("in", ["EN", "hi", "en"]) == ["en", "hi"]
    assert validate_output_languages("us", ["en"]) == ["en"]
    with pytest.raises(ValueError, match="'hi' is not available for region 'us'"):
        validate_output_languages("us", ["en", "hi"])
    with pytest.raises(ValueError):
        validate_output_languages("in", ["ta"])
    with pytest.raises(ValueError, match="at least one"):
        validate_output_languages("in", [])


def test_quiet_hours_and_min_scores() -> None:
    assert validate_quiet_hours(None, None) == (None, None)
    assert validate_quiet_hours("22:00", "07:00") == ("22:00", "07:00")
    for bad in (("22:00", None), ("25:00", "07:00"), ("9:00", "17:00"), ("08:00", "08:00")):
        with pytest.raises(ValueError):
            validate_quiet_hours(*bad)
    assert in_quiet_hours("23:30", "22:00", "07:00") and in_quiet_hours("03:00", "22:00", "07:00")
    assert not in_quiet_hours("07:00", "22:00", "07:00") and not in_quiet_hours(
        "12:00", "22:00", "07:00"
    )
    assert in_quiet_hours("13:00", "12:00", "14:00") and not in_quiet_hours(
        "14:00", "12:00", "14:00"
    )
    assert not in_quiet_hours("13:00", None, None)
    assert validate_min_scores(70, 50) == (70, 50)
    assert validate_min_scores(60, 60) == (60, 60)
    with pytest.raises(ValueError, match="must not exceed"):
        validate_min_scores(50, 70)
    with pytest.raises(ValueError):
        validate_min_scores(101, 50)
    with pytest.raises(ValueError):
        validate_min_scores(70, -1)


def test_channels_by_event() -> None:
    assert set(DEFAULT_CHANNELS_BY_EVENT) == {e.value for e in NotificationEvent}
    assert all(v == ["email"] for v in DEFAULT_CHANNELS_BY_EVENT.values())
    out = validate_channels_by_event({"high_fit_match": ["slack", "email", "slack"], "digest": []})
    assert out == {"high_fit_match": ["slack", "email"], "digest": []}
    with pytest.raises(ValueError, match="unknown notification event"):
        validate_channels_by_event({"birthday": ["email"]})
    with pytest.raises(ValueError, match="unknown channel"):
        validate_channels_by_event({"digest": ["pager"]})
    with pytest.raises(ValueError, match="must be a list"):
        validate_channels_by_event({"digest": "email"})
