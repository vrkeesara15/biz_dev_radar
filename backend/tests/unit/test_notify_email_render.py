"""M4-10: email templates render the SPEC 7 content (title, buyer, value, due date in the
user's tz with a countdown, fit score + 3 bullets, top gap, links, CAN-SPAM footer)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.core.config import Settings
from app.notify.render import (
    CATEGORY_LABELS,
    TEMPLATES,
    email_context,
    money_display,
    render_email,
    template_for,
)
from app.notify.unsubscribe import verify_unsubscribe_token

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")

MATCH_PAYLOAD: dict[str, Any] = {
    "title": "Cloud migration services",
    "buyer": "Department of Energy",
    "value_amount": "1200000",
    "value_currency": "USD",
    "response_due_at": DUE.isoformat(),
    "buyer_tz": "America/New_York",
    "score": 82.5,
    "band": "high",
    "deep_link": "https://app.example/app/opportunities/abc",
    "actions": {
        "pursue": "https://api.example/a/1",
        "watch": "https://api.example/a/2",
        "pass": "https://api.example/a/3",
        "assign": "https://api.example/a/4",
    },
    "rationale": {
        "fit_summary": ["NAICS 541511 exact", "Two similar DOE awards", "Remote delivery ok"],
        "gaps": [{"gap": "No FedRAMP Moderate", "suggested_fix": "Team with an authorised CSP"}],
    },
}


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _context(settings: Settings, payload: dict[str, Any], event: str, tz: str = "Asia/Kolkata"):  # type: ignore[no-untyped-def]
    return email_context(
        payload,
        event_type=event,
        settings=settings,
        tenant_id=TENANT,
        user_id=USER,
        user_tz=tz,
        now=NOW,
    )


def test_high_match_email_carries_every_spec_7_element(settings: Settings) -> None:
    mail = render_email("high_fit_match", _context(settings, MATCH_PAYLOAD, "high_fit_match"))
    assert mail.subject == "High fit 82.5: Cloud migration services"
    for part in (mail.html, mail.text):
        assert "Cloud migration services" in part
        assert "Department of Energy" in part
        assert "$1,200,000" in part
        # due date in the buyer's zone AND the user's, with a countdown
        assert "Oct 14, 2:00 PM EDT" in part
        assert "11:30 PM IST" in part
        assert "3d 6h" in part
        assert "82.5" in part
        assert "No FedRAMP Moderate" in part
        assert "Team with an authorised CSP" in part
        assert "https://app.example/app/opportunities/abc" in part
        for action_url in MATCH_PAYLOAD["actions"].values():
            assert action_url in part
    # exactly the three rationale bullets, in order
    bullets = [line for line in mail.text.splitlines() if line.startswith("- ")]
    assert bullets == [
        "- NAICS 541511 exact",
        "- Two similar DOE awards",
        "- Remote delivery ok",
    ]


def test_rationale_is_capped_at_three_bullets(settings: Settings) -> None:
    payload = {**MATCH_PAYLOAD, "rationale": ["one", "two", "three", "four"]}
    mail = render_email("high_fit_match", _context(settings, payload, "high_fit_match"))
    assert [line for line in mail.text.splitlines() if line.startswith("- ")] == [
        "- one",
        "- two",
        "- three",
    ]


def test_footer_has_a_signed_per_category_unsubscribe_link(settings: Settings) -> None:
    context = _context(settings, MATCH_PAYLOAD, "high_fit_match")
    mail = render_email("high_fit_match", context)
    category_url = context["unsubscribe_url"]
    all_url = context["unsubscribe_all_url"]
    assert category_url != all_url
    for part in (mail.html, mail.text):
        assert category_url in part
        assert all_url in part
        assert settings.email_postal_address in part
        assert "high-fit matches" in part
    claims = verify_unsubscribe_token(
        category_url.rsplit("/", 1)[-1], settings.auth_secret, now=NOW
    )
    assert (claims.category, claims.user_id, claims.tenant_id) == ("high_fit_match", USER, TENANT)
    assert (
        verify_unsubscribe_token(all_url.rsplit("/", 1)[-1], settings.auth_secret, now=NOW).category
        == "all"
    )


def test_digest_lists_every_item_with_its_own_due_and_score(settings: Settings) -> None:
    payload = {
        "digest_period": "daily",
        "items": [
            {
                "title": "Data centre refresh",
                "buyer": "GSA",
                "score": 64,
                "link": "https://app.example/app/opportunities/one",
                "response_due_at": DUE.isoformat(),
                "buyer_tz": "America/New_York",
            },
            {
                "title": "Network operations support",
                "buyer": "NIC",
                "score": 58,
                "link": "https://app.example/app/opportunities/two",
                "value_amount": "25000000",
                "value_currency": "INR",
            },
        ],
    }
    mail = render_email("digest", _context(settings, payload, "digest"))
    assert mail.subject == "BidRadar daily digest: 2 opportunities"
    for part in (mail.html, mail.text):
        assert "Data centre refresh" in part
        assert "Network operations support" in part
        assert "https://app.example/app/opportunities/two" in part
        assert "3d 6h" in part
    assert "digests" in mail.text  # footer names the category


def test_digest_subject_is_singular_for_one_item(settings: Settings) -> None:
    payload = {"items": [{"title": "Only one", "link": "https://x/1"}]}
    mail = render_email("digest", _context(settings, payload, "digest"))
    assert mail.subject == "BidRadar daily digest: 1 opportunity"


def test_amendment_renders_the_field_level_diff(settings: Settings) -> None:
    payload = {
        "title": "Cloud migration services",
        "diff": {
            "response_due_at": {"before": "2026-10-14", "after": "2026-10-28"},
            "documents": {"before": 3, "after": 4},
        },
    }
    mail = render_email("amendment", _context(settings, payload, "amendment"))
    assert mail.subject == "Amendment: Cloud migration services"
    for part in (mail.html, mail.text):
        assert "response_due_at" in part
        assert "2026-10-14" in part and "2026-10-28" in part
        assert "documents" in part


def test_amendment_accepts_a_diff_list(settings: Settings) -> None:
    payload = {
        "title": "T",
        "changes": [{"field": "status", "before": "open", "after": "cancelled"}],
    }
    mail = render_email("amendment", _context(settings, payload, "amendment"))
    assert "status" in mail.text and "cancelled" in mail.text


@pytest.mark.parametrize(
    ("event", "payload", "expected_subject"),
    [
        (
            "deadline_reminder",
            {"title": "Bridge inspection", "response_due_at": DUE.isoformat()},
            "Due 3d 6h: Bridge inspection",
        ),
        (
            "agent_question",
            {"title": "Cloud migration services", "headline": "Agent needs input"},
            "Agent needs input: Cloud migration services",
        ),
        (
            "approval_request",
            {"title": "Cloud migration services"},
            "Draft ready for review: Cloud migration services",
        ),
        (
            "registration_expiry",
            {"registration": "SAM registration", "response_due_at": DUE.isoformat()},
            "SAM registration expires 3d 6h",
        ),
        (
            "adapter_failing",
            {"source_id": "cppp", "failures": 3, "error": "HTTP 503"},
            "[ops] cppp has failed 3 runs",
        ),
        ("something_new", {"title": "Unmapped event"}, "Unmapped event"),
    ],
)
def test_every_event_renders_its_template(
    settings: Settings, event: str, payload: dict[str, Any], expected_subject: str
) -> None:
    mail = render_email(event, _context(settings, payload, event))
    assert mail.subject == expected_subject
    assert mail.html.startswith("<!doctype html>") or "<!doctype html>" in mail.html
    assert mail.text.strip()
    assert "unsubscribe" in mail.text.lower()


def test_unmapped_event_falls_back_to_generic_and_unsubscribes_from_all(
    settings: Settings,
) -> None:
    assert template_for("something_new") == "generic"
    context = _context(settings, {"title": "Unmapped"}, "something_new")
    token = context["unsubscribe_url"].rsplit("/", 1)[-1]
    assert verify_unsubscribe_token(token, settings.auth_secret, now=NOW).category == "all"


def test_every_mapped_event_has_a_category_label() -> None:
    assert set(TEMPLATES) - {"adapter.failing"} <= set(CATEGORY_LABELS)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, None),
        ({"value": "negotiated"}, "negotiated"),
        ({"value_amount": "1200000", "value_currency": "USD"}, "$1,200,000"),
        ({"value_amount": "12000000", "value_currency": "INR"}, "₹1,20,00,000"),
        (
            {"value_min": "500000", "value_max": "1200000", "value_currency": "USD"},
            "$500,000 - $1,200,000",
        ),
        ({"value_min": "900", "value_max": "900", "currency": "USD"}, "$900"),
        ({"value_amount": "not a number"}, None),
    ],
)
def test_money_display(payload: dict[str, Any], expected: str | None) -> None:
    assert money_display(payload) == expected


def test_overdue_and_missing_dates_are_safe(settings: Settings) -> None:
    overdue = {"title": "Late", "response_due_at": (datetime(2026, 10, 10, tzinfo=UTC)).isoformat()}
    mail = render_email("deadline_reminder", _context(settings, overdue, "deadline_reminder"))
    assert "overdue" in mail.subject
    no_date = render_email(
        "high_fit_match", _context(settings, {"title": "No date", "score": 71}, "high_fit_match")
    )
    assert "Response due" not in no_date.text


def test_naive_and_unparseable_dates_do_not_break_rendering(settings: Settings) -> None:
    context = _context(settings, {"title": "T", "response_due_at": "not-a-date"}, "high_fit_match")
    assert context["due"] is None
    naive = _context(
        settings, {"title": "T", "response_due_at": "2026-10-14T18:00:00"}, "high_fit_match"
    )
    assert naive["due"] is not None and naive["countdown"] == "3d 6h"


def test_html_escapes_untrusted_notice_text(settings: Settings) -> None:
    payload = {"title": "<script>alert(1)</script>", "score": 80, "band": "high"}
    mail = render_email("high_fit_match", _context(settings, payload, "high_fit_match"))
    assert "<script>alert(1)</script>" not in mail.html
    assert "&lt;script&gt;" in mail.html
