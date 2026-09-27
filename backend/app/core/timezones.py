"""Time-zone and locale validation. Pure logic."""

from __future__ import annotations

import re
from zoneinfo import ZoneInfoNotFoundError, available_timezones

_LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2}|-\d{3})?$")
_TZ_CACHE: frozenset[str] | None = None


def known_timezones() -> frozenset[str]:
    global _TZ_CACHE
    if _TZ_CACHE is None:
        _TZ_CACHE = frozenset(available_timezones())
    return _TZ_CACHE


def validate_timezone(name: str) -> str:
    """Return the canonical IANA name or raise ZoneInfoNotFoundError."""
    candidate = name.strip()
    if candidate not in known_timezones():
        raise ZoneInfoNotFoundError(candidate)
    return candidate


def validate_locale(tag: str) -> str:
    """BCP-47-ish language tag such as en, en-US, hi-IN. Raises ValueError."""
    candidate = tag.strip()
    if not _LOCALE_RE.match(candidate):
        raise ValueError(f"invalid locale {tag!r}")
    return candidate
