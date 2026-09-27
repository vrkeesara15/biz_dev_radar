"""M4-07 unit: candidate terms and the keyword lift computation (pure)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.matching.learning import (
    EXCLUDE,
    INCLUDE,
    MAX_DELTA,
    Observation,
    candidate_terms,
    delta_for,
    keyword_lift,
)


def _obs(terms: str, positive: bool) -> Observation:
    return Observation(terms=frozenset(terms.split(",")), positive=positive)


# --- candidate terms ---------------------------------------------------------------------


def test_candidate_terms_keeps_unigrams_and_bigrams() -> None:
    terms = candidate_terms("Cloud Migration Services for the Treasury")
    assert "cloud" in terms and "migration" in terms
    assert "cloud migration" in terms
    assert "migration treasury" not in terms  # "services for the" is a gap, never bridged
    assert "for" not in terms and "the" not in terms  # stop words


def test_candidate_terms_drops_short_tokens_numbers_and_boilerplate() -> None:
    terms = candidate_terms("RFP 47QF26R0001: the contractor shall provide 24 janitorial units")
    assert "janitorial" in terms
    assert "contractor" not in terms and "shall" not in terms and "provide" not in terms
    assert "rfp" not in terms  # three characters
    assert not any(t[0].isdigit() for t in terms)


def test_candidate_terms_merges_several_texts_and_owner_keywords() -> None:
    terms = candidate_terms(
        "Cloud migration",
        "Kubernetes orchestration",
        None,
        extra=["  Zero   Trust Architecture ", ""],
    )
    assert {"cloud", "kubernetes", "zero trust architecture"} <= terms
    assert "migration kubernetes" not in terms  # no bigram across two texts


def test_candidate_terms_is_capped() -> None:
    text = " ".join(f"alpha{i}word" for i in range(200))
    assert len(candidate_terms(text, max_terms=10)) == 10


# --- lift --------------------------------------------------------------------------------


def test_lift_promotes_a_term_that_tracks_the_thumbs_up() -> None:
    observations = [
        _obs("cloud,migration", True),
        _obs("cloud,migration", True),
        _obs("cloud,devops", True),
        _obs("janitorial", False),
        _obs("janitorial", False),
        _obs("janitorial,lawn", False),
    ]
    lifts = {t.term: t for t in keyword_lift(observations)}
    assert lifts["cloud"].kind == INCLUDE
    assert lifts["cloud"].support == 3 and lifts["cloud"].positives == 3
    assert lifts["cloud"].baseline == Decimal("0.5")
    assert lifts["cloud"].rate == Decimal("1")
    assert lifts["cloud"].lift == Decimal("0.5")
    assert lifts["cloud"].delta_weight == Decimal("1.0")
    assert lifts["janitorial"].kind == EXCLUDE
    assert lifts["janitorial"].lift == Decimal("-0.5")
    assert lifts["janitorial"].delta_weight == Decimal("-1.0")
    assert lifts["janitorial"].evidence() == {
        "support": 3,
        "positives": 0,
        "negatives": 3,
        "rate": 0.0,
        "baseline": 0.5,
        "lift": -0.5,
    }


def test_lift_needs_support() -> None:
    observations = [
        _obs("cloud,rare", True),
        _obs("cloud", True),
        _obs("cloud", True),
        _obs("lawn", False),
        _obs("lawn", False),
        _obs("lawn", False),
    ]
    terms = {t.term for t in keyword_lift(observations)}
    assert "rare" not in terms  # only one observation
    assert {"cloud", "lawn"} <= terms


def test_a_term_in_every_notice_explains_nothing() -> None:
    observations = [
        _obs("services,cloud", True),
        _obs("services,cloud", True),
        _obs("services,cloud", True),
        _obs("services,lawn", False),
        _obs("services,lawn", False),
        _obs("services,lawn", False),
    ]
    terms = {t.term for t in keyword_lift(observations)}
    assert "services" not in terms  # present in all 6: its rate IS the baseline
    assert terms == {"cloud", "lawn"}


def test_a_weak_lift_is_not_worth_asking_about() -> None:
    observations = [
        _obs("cloud", True),
        _obs("cloud", True),
        _obs("cloud", False),
        _obs("lawn", True),
        _obs("lawn", True),
        _obs("lawn", False),
    ]
    assert keyword_lift(observations) == []  # both terms sit exactly on the baseline


def test_too_little_feedback_yields_nothing() -> None:
    assert keyword_lift([]) == []
    assert keyword_lift([_obs("cloud", True), _obs("cloud", True)]) == []


def test_results_are_ordered_by_absolute_lift() -> None:
    observations = [
        _obs("strong,mild", True),
        _obs("strong,mild", True),
        _obs("strong", True),
        _obs("mild", False),
        _obs("other", False),
        _obs("other", False),
        _obs("other", False),
    ]
    lifts = keyword_lift(observations, min_support=3, min_lift=Decimal("0.05"))
    assert [t.term for t in lifts] == sorted(
        [t.term for t in lifts],
        key=lambda term: -abs(next(x.lift for x in lifts if x.term == term)),
    )
    assert abs(lifts[0].lift) >= abs(lifts[-1].lift)


@pytest.mark.parametrize(
    ("lift", "expected"),
    [
        (Decimal("0.5"), Decimal("1.0")),
        (Decimal("-0.5"), Decimal("-1.0")),
        (Decimal("0.17"), Decimal("0.3")),
        (Decimal("1"), MAX_DELTA),
        (Decimal("-1"), -MAX_DELTA),
        (Decimal("0"), Decimal("0.0")),
    ],
)
def test_delta_weight_scaling_and_clamp(lift: Decimal, expected: Decimal) -> None:
    assert delta_for(lift) == expected
