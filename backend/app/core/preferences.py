"""Profile and user preferences (SPEC 4.6, 6, 7). Pure validation and defaults.

DEFAULT_SCORING_WEIGHTS      SPEC 6 stage-2 weights, sum 100
validate_scoring_weights()   same keys, non-negative ints, sum == 100
DEFAULT_BID_NO_BID_WEIGHTS   SPEC 8 agent 4 scorecard criteria, sum 100
allowed_output_languages()   ['en'] everywhere, 'hi' only for IN profiles
validate_quiet_hours()       HH:MM strings; wrap past midnight allowed
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from app.core.config import Region

# SPEC section 6, stage 2 (editable per profile).
DEFAULT_SCORING_WEIGHTS: dict[str, int] = {
    "code_match": 25,
    "semantic_similarity": 25,
    "keyword_match": 10,
    "eligibility": 15,
    "value_fit": 5,
    "geography": 5,
    "buyer_affinity": 5,
    "past_performance_relevance": 10,
}

# SPEC section 8, agent 4 scorecard criteria (fit, eligibility, capacity, competition/
# incumbent, value, win probability); weights editable per profile, sum 100.
DEFAULT_BID_NO_BID_WEIGHTS: dict[str, int] = {
    "fit": 25,
    "eligibility": 20,
    "capacity": 15,
    "competition": 15,
    "value": 10,
    "win_probability": 15,
}

WEIGHT_TOTAL = 100

DEFAULT_OUTPUT_LANGUAGES: tuple[str, ...] = ("en",)
OUTPUT_LANGUAGES_BY_REGION: dict[Region, frozenset[str]] = {
    Region.US: frozenset({"en"}),
    Region.IN: frozenset({"en", "hi"}),
}

DEFAULT_MIN_SCORE_INSTANT = 70
DEFAULT_MIN_SCORE_DIGEST = 50
DEFAULT_DIGEST_TIME = "08:00"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    WHATSAPP = "whatsapp"
    WEB_PUSH = "web_push"


class NotificationEvent(StrEnum):
    HIGH_FIT_MATCH = "high_fit_match"
    DIGEST = "digest"
    AMENDMENT = "amendment"
    DEADLINE_REMINDER = "deadline_reminder"
    PURSUIT_UPDATE = "pursuit_update"
    AGENT_QUESTION = "agent_question"
    APPROVAL_REQUEST = "approval_request"
    REGISTRATION_EXPIRY = "registration_expiry"


DEFAULT_CHANNELS_BY_EVENT: dict[str, list[str]] = {
    event.value: ["email"] for event in NotificationEvent
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_weights(
    weights: Mapping[str, object], expected: Mapping[str, int], label: str
) -> dict[str, int]:
    keys = set(weights)
    if keys != set(expected):
        missing = sorted(set(expected) - keys)
        extra = sorted(keys - set(expected))
        raise ValueError(
            f"{label} must have exactly the keys {sorted(expected)}; "
            f"missing={missing} extra={extra}"
        )
    out: dict[str, int] = {}
    for key in expected:
        value = weights[key]
        if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
            raise ValueError(f"{label}[{key}] must be a whole number")
        if value < 0:
            raise ValueError(f"{label}[{key}] must be >= 0")
        out[key] = int(value)
    total = sum(out.values())
    if total != WEIGHT_TOTAL:
        raise ValueError(f"{label} must sum to {WEIGHT_TOTAL}, got {total}")
    return out


def validate_scoring_weights(weights: Mapping[str, object]) -> dict[str, int]:
    return _validate_weights(weights, DEFAULT_SCORING_WEIGHTS, "scoring_weights")


def validate_bid_no_bid_weights(weights: Mapping[str, object]) -> dict[str, int]:
    return _validate_weights(weights, DEFAULT_BID_NO_BID_WEIGHTS, "bid_no_bid_weights")


def allowed_output_languages(region: Region | str) -> frozenset[str]:
    return OUTPUT_LANGUAGES_BY_REGION[Region(region)]


def validate_output_languages(region: Region | str, languages: list[str]) -> list[str]:
    allowed = allowed_output_languages(region)
    out: list[str] = []
    for raw in languages:
        lang = raw.strip().lower()
        if lang not in allowed:
            raise ValueError(
                f"language {raw!r} is not available for region {Region(region).value!r}; "
                f"allowed: {sorted(allowed)}"
            )
        if lang not in out:
            out.append(lang)
    if not out:
        raise ValueError("at least one output language is required")
    return out


def validate_hhmm(value: str, label: str = "time") -> str:
    text = value.strip()
    if not _HHMM.match(text):
        raise ValueError(f"{label} must be HH:MM (24-hour)")
    return text


def validate_quiet_hours(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """Both or neither; equal start/end is rejected (would silence everything or nothing)."""
    if (start is None) != (end is None):
        raise ValueError("quiet_hours_start and quiet_hours_end go together")
    if start is None or end is None:
        return None, None
    start, end = validate_hhmm(start, "quiet_hours_start"), validate_hhmm(end, "quiet_hours_end")
    if start == end:
        raise ValueError("quiet hours must not start and end at the same time")
    return start, end


def in_quiet_hours(hhmm: str, start: str | None, end: str | None) -> bool:
    """True when the local time HH:MM falls inside [start, end), wrapping past midnight."""
    if start is None or end is None:
        return False
    if start < end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end


def validate_channels_by_event(value: Mapping[str, object]) -> dict[str, list[str]]:
    events = {e.value for e in NotificationEvent}
    channels = {c.value for c in NotificationChannel}
    out: dict[str, list[str]] = {}
    for event, chosen in value.items():
        if event not in events:
            raise ValueError(f"unknown notification event {event!r}")
        if not isinstance(chosen, list | tuple):
            raise ValueError(f"channels for {event!r} must be a list")
        cleaned: list[str] = []
        for channel in chosen:
            if not isinstance(channel, str) or channel not in channels:
                raise ValueError(f"unknown channel {channel!r} for {event!r}")
            if channel not in cleaned:
                cleaned.append(channel)
        out[event] = cleaned
    return out


def validate_min_scores(instant: int, digest: int) -> tuple[int, int]:
    for label, value in (("min_score_instant", instant), ("min_score_digest", digest)):
        if not 0 <= value <= 100:
            raise ValueError(f"{label} must be between 0 and 100")
    if digest > instant:
        raise ValueError("min_score_digest must not exceed min_score_instant")
    return instant, digest
