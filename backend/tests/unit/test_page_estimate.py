"""M5-10: the deterministic page estimate behind the red team's page-limit overruns."""

from __future__ import annotations

import pytest
from app.core.page_estimate import (
    BASE_WORDS_PER_PAGE,
    MAX_WORDS_PER_PAGE,
    MIN_WORDS_PER_PAGE,
    count_words,
    estimate_pages,
    fits,
    parse_margin_inches,
    words_per_page,
)


def words(n: int) -> str:
    return " ".join(f"word{i}" for i in range(n))


def test_words_are_whitespace_separated_tokens() -> None:
    assert count_words("") == 0
    assert count_words("  one   two\nthree\t four ") == 4
    assert count_words("- a bullet line") == 4


@pytest.mark.parametrize(
    ("margins", "expected"),
    [
        (None, None),
        ("", None),
        ("no margin rule stated", None),
        ("1 inch", 1.0),
        ('0.75"', 0.75),
        ("1 inch top and 0.5 inch sides", 0.5),
        ("2.54 cm", 1.0),
        ("25 mm", pytest.approx(0.984, abs=0.01)),
        ("0.1 inch", 0.25),  # clamped to the minimum
        ("9 inches", 2.5),  # clamped to the maximum
    ],
)
def test_margins_parse_to_inches(margins: str | None, expected: float | None) -> None:
    assert parse_margin_inches(margins) == expected


def test_the_reference_page_is_twelve_point_with_one_inch_margins() -> None:
    assert words_per_page() == BASE_WORDS_PER_PAGE
    assert words_per_page(12, "1 inch") == BASE_WORDS_PER_PAGE


def test_smaller_type_and_narrower_margins_fit_more_words() -> None:
    assert words_per_page(10) > words_per_page(12) > words_per_page(14)
    assert words_per_page(12, "0.5 inch") > words_per_page(12, "1 inch")
    assert words_per_page(12, "2 inch") < words_per_page(12, "1 inch")


def test_absurd_rules_are_clamped() -> None:
    assert words_per_page(2) == MAX_WORDS_PER_PAGE  # the words-per-page ceiling
    assert words_per_page(72) == words_per_page(24)  # the font size is clamped first
    # tiny type inside huge margins still never drops below the floor
    assert words_per_page(24, "9 inches") == MIN_WORDS_PER_PAGE


def test_an_empty_body_is_zero_pages_and_never_over() -> None:
    estimate = estimate_pages("", limit=1)
    assert estimate.pages == 0.0 and estimate.words == 0
    assert not estimate.over and estimate.over_by == 0.0 and fits(estimate)


def test_a_short_section_fits_its_budget() -> None:
    estimate = estimate_pages(words(400), limit=2)
    assert estimate.pages == 0.8
    assert not estimate.over
    assert estimate.words_to_cut == 0


def test_an_overrun_reports_how_much_must_go() -> None:
    estimate = estimate_pages(words(1600), limit=2)
    assert estimate.words_per_page == BASE_WORDS_PER_PAGE
    assert estimate.pages == 3.2
    assert estimate.over and estimate.over_by == 1.2
    assert estimate.words_to_cut == 600
    assert not fits(estimate)


def test_the_same_text_fits_once_the_solicitation_allows_ten_point_type() -> None:
    body = words(1300)
    assert estimate_pages(body, limit=2).over
    assert not estimate_pages(body, font_size_pt=10, margins="0.5 inch", limit=2).over


def test_no_limit_means_no_overrun() -> None:
    estimate = estimate_pages(words(5000))
    assert estimate.limit is None and not estimate.over
    assert estimate.over_by == 0.0 and estimate.words_to_cut == 0
    assert estimate.as_dict()["pages"] == estimate.pages
