"""M4-14 unit: SPEC 7's routing table, channel resolution and the per-user minimum score.

Table-driven: every row of SPEC 7's event table is asserted against ROUTES, and channel
resolution is exercised over the precedence ladder (alert rule > user prefs > default).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from app.core.preferences import NotificationEvent as Category
from app.core.roles import Role
from app.notify.registry import CHANNEL_NAMES, normalize_channels
from app.notify.router import (
    DIGEST,
    INSTANT,
    ROUTES,
    channels_for,
    mode_for,
    route_for,
    score_allows,
)
from app.services.matching.alerts import AlertDecision

# (bus event, category, channels, mode, audience) exactly as SPEC 7's table reads
SPEC_TABLE: tuple[tuple[str, str, tuple[str, ...], str, str], ...] = (
    (
        "match.high",
        Category.HIGH_FIT_MATCH.value,
        ("in_app", "email", "slack", "teams"),
        INSTANT,
        "match",
    ),
    ("match.medium", Category.DIGEST.value, ("email",), DIGEST, "match"),
    (
        "opportunity.amended",
        Category.AMENDMENT.value,
        ("in_app", "email", "slack"),
        INSTANT,
        "tracked",
    ),
    (
        "agent.draft_ready",
        Category.APPROVAL_REQUEST.value,
        ("in_app", "email"),
        INSTANT,
        "assignee",
    ),
    (
        "agent.needs_input",
        Category.AGENT_QUESTION.value,
        ("in_app", "email"),
        INSTANT,
        "assignee",
    ),
    (
        "registration.expiring",
        Category.REGISTRATION_EXPIRY.value,
        ("email",),
        INSTANT,
        "owner",
    ),
    ("adapter.failing", "adapter_failing", ("slack", "email"), INSTANT, "ops"),
)


def _prefs(**kwargs: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "channels_by_event": {},
        "min_score_instant": 70,
        "min_score_digest": 50,
        "tz": "UTC",
        "quiet_hours_start": None,
        "quiet_hours_end": None,
        "digest_time": "08:00",
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def _decision(channels: tuple[str, ...], mode: str = INSTANT, **kwargs: object) -> AlertDecision:
    return AlertDecision(
        rule_id=uuid.uuid4(),
        name="rule",
        channels=channels,
        mode=mode,
        min_score=0,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(("event", "category", "channels", "mode", "audience"), SPEC_TABLE)
def test_spec_7_table(
    event: str, category: str, channels: tuple[str, ...], mode: str, audience: str
) -> None:
    route = route_for(event)
    assert route is not None, f"SPEC 7 row {event} is not routed"
    assert route.category == category
    assert route.channels == channels
    assert route.mode == mode
    assert route.audience == audience


def test_the_table_has_no_extra_rows_and_no_unknown_channel() -> None:
    assert set(ROUTES) == {row[0] for row in SPEC_TABLE}
    for route in ROUTES.values():
        assert set(route.channels) <= set(CHANNEL_NAMES), route.event
        assert route.mode in {INSTANT, DIGEST}


def test_unrouted_events_are_ignored() -> None:
    assert route_for("opportunity.created") is None
    assert route_for("profile.changed") is None


# --- channel precedence ----------------------------------------------------------------


def test_default_channels_when_the_user_said_nothing() -> None:
    route = ROUTES["match.high"]
    assert channels_for(route, None) == ("in_app", "email", "slack", "teams")
    assert channels_for(route, _prefs()) == ("in_app", "email", "slack", "teams")  # type: ignore[arg-type]


def test_user_preferences_replace_the_defaults_and_are_normalised() -> None:
    route = ROUTES["match.high"]
    prefs = _prefs(channels_by_event={Category.HIGH_FIT_MATCH.value: ["email", "web_push"]})
    assert channels_for(route, prefs) == ("email", "push")  # type: ignore[arg-type]
    # a preference for another event does not touch this one
    other = _prefs(channels_by_event={Category.AMENDMENT.value: ["slack"]})
    assert channels_for(route, other) == route.channels  # type: ignore[arg-type]


def test_an_empty_preference_list_mutes_the_event() -> None:
    prefs = _prefs(channels_by_event={Category.HIGH_FIT_MATCH.value: []})
    assert channels_for(ROUTES["match.high"], prefs) == ()  # type: ignore[arg-type]


def test_whatsapp_is_dropped_until_a_channel_exists() -> None:
    prefs = _prefs(channels_by_event={Category.HIGH_FIT_MATCH.value: ["whatsapp", "email"]})
    assert channels_for(ROUTES["match.high"], prefs) == ("email",)  # type: ignore[arg-type]
    assert normalize_channels(["whatsapp"]) == ()


def test_alert_rules_beat_both_and_are_unioned() -> None:
    route = ROUTES["match.medium"]
    prefs = _prefs(channels_by_event={Category.DIGEST.value: ["email"]})
    decisions = [_decision(("slack",)), _decision(("email", "in_app"))]
    assert channels_for(route, prefs, decisions) == ("in_app", "email", "slack")  # type: ignore[arg-type]


def test_mode_follows_the_rules_then_the_band() -> None:
    high, medium = ROUTES["match.high"], ROUTES["match.medium"]
    assert mode_for(high) == INSTANT and mode_for(medium) == DIGEST
    assert mode_for(medium, [_decision(("email",), INSTANT)]) == INSTANT
    assert mode_for(high, [_decision(("email",), DIGEST)]) == DIGEST
    # one instant rule among several is enough
    assert mode_for(medium, [_decision(("email",), DIGEST), _decision(("slack",), INSTANT)]) == (
        INSTANT
    )


# --- minimum score ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "score", "instant_floor", "digest_floor", "expected"),
    [
        ("match.high", 72, 70, 50, True),
        ("match.high", 70, 70, 50, True),
        ("match.high", 69.99, 70, 50, False),
        ("match.high", 85, 90, 50, False),
        ("match.medium", 55, 70, 50, True),
        ("match.medium", 49, 70, 50, False),
        ("match.medium", 60, 70, 60, True),
    ],
)
def test_minimum_score_per_user(
    event: str, score: float, instant_floor: int, digest_floor: int, expected: bool
) -> None:
    prefs = _prefs(min_score_instant=instant_floor, min_score_digest=digest_floor)
    assert score_allows(ROUTES[event], prefs, score) is expected  # type: ignore[arg-type]


def test_score_gate_is_open_without_prefs_or_without_a_score() -> None:
    assert score_allows(ROUTES["match.high"], None, 10) is True
    assert score_allows(ROUTES["match.high"], _prefs(), None) is True  # type: ignore[arg-type]
    # a non-match event carries no score and is never gated
    assert score_allows(ROUTES["registration.expiring"], _prefs(), None) is True  # type: ignore[arg-type]


def test_audience_roles_match_spec_7() -> None:
    assert ROUTES["match.high"].roles == (Role.TENANT_OWNER, Role.BID_MANAGER)
    assert ROUTES["registration.expiring"].roles == (Role.TENANT_OWNER,)
    assert ROUTES["agent.draft_ready"].roles == ()  # the assignee, not a role
