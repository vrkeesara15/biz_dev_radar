"""M4-08 unit: the saved-search filter set and its matcher (pure)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.matching.saved_search import SearchFilters, parse_query, text_matches
from app.core.matching.types import MatchOpportunity

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

NOTICE = MatchOpportunity(
    region="us",
    country="US",
    notice_type="rfp",
    title="Cloud migration services",
    summary="Move mainframe workloads to a commercial cloud.",
    status="open",
    buyer_org="Department of the Treasury",
    buyer_sub_org="Internal Revenue Service",
    naics=("541511",),
    response_due_at=NOW + timedelta(days=10),
)


def test_empty_filters_accept_everything() -> None:
    filters = SearchFilters.from_dict(None)
    assert filters.is_empty
    assert filters.as_dict() == {}
    assert filters.matches(NOTICE, now=NOW) is True


def test_from_dict_accepts_the_query_string_spellings() -> None:
    filters = SearchFilters.from_dict(
        {
            "q": " cloud migration ",
            "region": "US",
            "type": "rfp,combined",
            "naics": ["541511", " 541512 "],
            "status": "open",
            "buyer": "Internal Revenue Service",
            "due_before": "2026-12-31T00:00:00+00:00",
            "due_within_days": "30",
            "min_score": "60",
        }
    )
    assert filters.q == "cloud migration" and filters.region == "us"
    assert filters.notice_types == ("rfp", "combined")
    assert filters.naics == ("541511", "541512")
    assert filters.statuses == ("open",)
    assert filters.due_within_days == 30 and filters.min_score == 60
    assert filters.due_before == datetime(2026, 12, 31, tzinfo=UTC)
    # round-trips through the stored jsonb
    assert SearchFilters.from_dict(filters.as_dict()) == filters
    # the alternative key names the API may send
    assert SearchFilters.from_dict({"notice_types": ["rfp"], "statuses": ["open"]}) == (
        SearchFilters(notice_types=("rfp",), statuses=("open",))
    )


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"region": "us"}, True),
        ({"region": "in"}, False),
        ({"type": ["rfp"]}, True),
        ({"type": ["grant"]}, False),
        ({"status": ["open", "closing_soon"]}, True),
        ({"status": ["closed"]}, False),
        ({"naics": ["541511"]}, True),
        ({"naics": ["561730"]}, False),
        ({"buyer": ["Internal Revenue Service"]}, True),
        ({"buyer": ["Department of Energy"]}, False),
        ({"due_within_days": 30}, True),
        ({"due_within_days": 3}, False),
        ({"due_before": "2026-12-31T00:00:00+00:00"}, True),
        ({"due_before": "2026-09-28T00:00:00+00:00"}, False),
        ({"q": "cloud migration"}, True),
        ({"q": "janitorial"}, False),
        ({"q": "cloud -mainframe"}, False),
        ({"q": '"cloud migration"'}, True),
    ],
)
def test_single_filters(filters: dict[str, object], expected: bool) -> None:
    assert SearchFilters.from_dict(filters).matches(NOTICE, now=NOW) is expected


def test_min_score_needs_a_score() -> None:
    filters = SearchFilters.from_dict({"min_score": 70})
    assert filters.matches(NOTICE, now=NOW) is False  # no score given
    assert filters.matches(NOTICE, now=NOW, score=Decimal("69.99")) is False
    assert filters.matches(NOTICE, now=NOW, score=70) is True


def test_a_notice_without_a_deadline_fails_a_deadline_filter() -> None:
    undated = MatchOpportunity(
        region="us", country="US", notice_type="rfp", title="Forecast", response_due_at=None
    )
    assert SearchFilters.from_dict({"due_within_days": 30}).matches(undated, now=NOW) is False
    assert (
        SearchFilters.from_dict({"due_before": "2027-01-01T00:00:00+00:00"}).matches(
            undated, now=NOW
        )
        is False
    )


def test_every_filter_must_pass() -> None:
    both_good = SearchFilters.from_dict({"region": "us", "naics": ["541511"]})
    one_bad = SearchFilters.from_dict({"region": "us", "naics": ["999999"]})
    assert both_good.matches(NOTICE, now=NOW) is True
    assert one_bad.matches(NOTICE, now=NOW) is False


# --- the little query parser ----------------------------------------------------------------


def test_parse_query_handles_phrases_and_exclusions() -> None:
    required, excluded = parse_query('"cloud migration" devops -janitorial -"lawn care"')
    assert required == ("cloud migration", "devops")
    assert excluded == ("janitorial", "lawn care")


def test_parse_query_drops_bare_operators() -> None:
    assert parse_query("cloud OR devops AND kubernetes") == (
        ("cloud", "devops", "kubernetes"),
        (),
    )


def test_text_matches_is_whole_word_and_punctuation_tolerant() -> None:
    assert text_matches("cloud", "Cloud migration services.") is True
    assert text_matches("cloud", "Cloudburst monitoring") is False
    assert text_matches("migration services", "Cloud migration services,") is True
    assert text_matches("cloud -services", "Cloud migration services") is False
    assert text_matches("", "anything") is True
