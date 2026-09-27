"""Auto key dates (SPEC 9, M6-02): the pure table every pursuit's calendar hangs off."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.key_dates import (
    AUTO_KINDS,
    DEADLINE_OFFSET_HOURS,
    KINDS,
    NoticeDates,
    auto_dates,
    derived_kinds,
    label_for,
)

DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _kinds(notice: NoticeDates, region: str = "us", now: datetime = NOW) -> list[str]:
    return [d.kind for d in auto_dates(notice, region, now)]


def test_us_pursuit_gets_the_four_derived_dates_in_order() -> None:
    dates = auto_dates(NoticeDates(response_due_at=DUE, source_tz="America/New_York"), "us", NOW)
    assert [d.kind for d in dates] == [
        "internal_draft",
        "internal_review",
        "internal_final",
        "portal_submission",
    ]
    by_kind = {d.kind: d for d in dates}
    assert by_kind["internal_draft"].at == DUE - timedelta(days=5)
    assert by_kind["internal_review"].at == DUE - timedelta(days=3)
    assert by_kind["internal_final"].at == DUE - timedelta(hours=48)
    assert by_kind["portal_submission"].at == DUE
    assert all(d.buyer_tz == "America/New_York" for d in dates)
    assert all(d.is_past is False for d in dates)
    assert by_kind["internal_draft"].note == "5 days before the deadline"
    assert by_kind["internal_final"].note == "2 days before the deadline"
    assert by_kind["portal_submission"].note == "the response deadline"
    assert by_kind["internal_review"].label == "Internal review"


def test_india_adds_emd_bg_ready_and_the_dsc_check() -> None:
    dates = auto_dates(NoticeDates(response_due_at=DUE, source_tz="Asia/Kolkata"), "in", NOW)
    by_kind = {d.kind: d for d in dates}
    assert set(by_kind) == {
        "emd_bg_ready",
        "internal_draft",
        "internal_review",
        "dsc_check",
        "internal_final",
        "portal_submission",
    }
    assert by_kind["emd_bg_ready"].at == DUE - timedelta(days=5)
    assert by_kind["dsc_check"].at == DUE - timedelta(days=3)
    assert by_kind["emd_bg_ready"].label == "EMD / bank guarantee ready"
    # the US set never carries the Indian pair
    assert "emd_bg_ready" not in _kinds(NoticeDates(response_due_at=DUE))


def test_the_buyers_own_dates_are_copied_never_invented() -> None:
    questions = DUE - timedelta(days=10)
    prebid = DUE - timedelta(days=9)
    dates = auto_dates(
        NoticeDates(response_due_at=DUE, questions_due_at=questions, prebid_meeting_at=prebid),
        "us",
        NOW,
    )
    by_kind = {d.kind: d for d in dates}
    assert by_kind["questions_due"].at == questions
    assert by_kind["prebid_meeting"].at == prebid
    assert by_kind["questions_due"].note == "stated by the buyer"
    # a notice that states neither gets neither
    assert _kinds(NoticeDates(response_due_at=DUE)) == [
        "internal_draft",
        "internal_review",
        "internal_final",
        "portal_submission",
    ]


def test_a_notice_without_a_deadline_yields_nothing_derived() -> None:
    assert auto_dates(NoticeDates(), "us", NOW) == []
    assert _kinds(NoticeDates(questions_due_at=DUE - timedelta(days=20))) == ["questions_due"]


def test_dates_already_past_are_returned_and_flagged() -> None:
    soon = NOW + timedelta(days=2, hours=12)  # the draft and review dates are behind us
    dates = {d.kind: d for d in auto_dates(NoticeDates(response_due_at=soon), "us", NOW)}
    assert dates["internal_draft"].is_past is True
    assert dates["internal_review"].is_past is True
    assert dates["internal_final"].is_past is False
    assert dates["portal_submission"].is_past is False
    # the deadline itself, exactly now, counts as past
    assert auto_dates(NoticeDates(response_due_at=NOW), "us", NOW)[-1].is_past is True


def test_everything_is_sorted_soonest_first() -> None:
    dates = auto_dates(
        NoticeDates(
            response_due_at=DUE,
            questions_due_at=DUE - timedelta(days=12),
            prebid_meeting_at=DUE - timedelta(days=1),
        ),
        "in",
        NOW,
    )
    assert [d.at for d in dates] == sorted(d.at for d in dates)
    assert dates[0].kind == "questions_due"
    assert dates[-1].kind == "portal_submission"


def test_region_is_case_insensitive_and_unknown_regions_take_the_us_set() -> None:
    assert derived_kinds("IN") == derived_kinds("in")
    assert derived_kinds("eu") == derived_kinds("us")


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        auto_dates(NoticeDates(response_due_at=datetime(2026, 10, 14, 18, 0)), "us", NOW)
    with pytest.raises(ValueError, match="now must be an aware datetime"):
        auto_dates(NoticeDates(response_due_at=DUE), "us", datetime(2026, 9, 27, 12, 0))


def test_the_kind_inventory_is_complete_and_labelled() -> None:
    assert (*AUTO_KINDS, "custom") == KINDS
    assert set(DEADLINE_OFFSET_HOURS) <= set(AUTO_KINDS)
    assert all(label_for(kind) for kind in KINDS)
    assert label_for("something_else") == "Key date"
