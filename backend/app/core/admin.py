"""Pure helpers for the platform-admin console (SPEC 3, 10.3, 10.4 screen 9).

Billing periods ('YYYY-MM' as written into usage_ledger.period by core.plan),
LLM cost arithmetic (usage_ledger stores integer micro-dollars, OQ-46), the
health roll-up shown on the console and the support-access window arithmetic.
No I/O lives here so the module stays covered by unit tests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

# usage_ledger.quantity is an int32, so LLM cost is metered in micro-dollars (OQ-46).
MICRO_USD = 1_000_000

PERIOD_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

# Support access is deliberately short-lived: a platform admin opens a window for one
# ticket, not a standing key to a tenant's data (SPEC 3 "no access ... unless granted").
MIN_SUPPORT_MINUTES = 5
MAX_SUPPORT_MINUTES = 480
DEFAULT_SUPPORT_MINUTES = 60

# Worst-first: the console shows the worst status of any adapter as the roll-up.
HEALTH_ORDER: tuple[str, ...] = ("failing", "degraded", "not_implemented", "unknown", "ok")


def current_period(now: datetime | None = None) -> str:
    """The 'YYYY-MM' usage period containing `now` (UTC)."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


def parse_period(value: str | None, *, now: datetime | None = None) -> str:
    """Validate a ?period=YYYY-MM query value; None means the current month."""
    if value is None or value == "":
        return current_period(now)
    text = value.strip()
    if not PERIOD_PATTERN.match(text):
        raise ValueError("period must be formatted YYYY-MM")
    return text


def shift_period(period: str, months: int) -> str:
    """The period `months` before (negative) or after (positive) `period`."""
    match = PERIOD_PATTERN.match(period)
    if match is None:
        raise ValueError("period must be formatted YYYY-MM")
    total = int(match.group(1)) * 12 + (int(match.group(2)) - 1) + months
    year, month = divmod(total, 12)
    if year < 1:
        raise ValueError("period out of range")
    return f"{year:04d}-{month + 1:02d}"


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """[start, end) UTC instants of a 'YYYY-MM' period."""
    match = PERIOD_PATTERN.match(period)
    if match is None:
        raise ValueError("period must be formatted YYYY-MM")
    start = datetime(int(match.group(1)), int(match.group(2)), 1, tzinfo=UTC)
    nxt = shift_period(period, 1)
    end = datetime(int(nxt[:4]), int(nxt[5:]), 1, tzinfo=UTC)
    return start, end


def usd_from_microusd(micro_usd: int | None) -> Decimal:
    """Micro-dollars as a Decimal number of USD, exact to six places."""
    return (Decimal(int(micro_usd or 0)) / MICRO_USD).quantize(Decimal("0.000001"))


def clamp_support_minutes(minutes: int | None) -> int:
    """Support-access window in minutes, clamped to the allowed band."""
    if minutes is None:
        return DEFAULT_SUPPORT_MINUTES
    return max(MIN_SUPPORT_MINUTES, min(MAX_SUPPORT_MINUTES, int(minutes)))


def support_access_expiry(granted_at: datetime, minutes: int | None) -> datetime:
    """When a grant opened at `granted_at` stops working."""
    return granted_at + timedelta(minutes=clamp_support_minutes(minutes))


def grant_is_active(
    expires_at: datetime, revoked_at: datetime | None, now: datetime | None = None
) -> bool:
    """A grant is usable while it is neither revoked nor expired."""
    if revoked_at is not None:
        return False
    moment = now or datetime.now(UTC)
    return expires_at > moment


def worst_health(statuses: Iterable[str]) -> str:
    """Roll several adapter health statuses up into the worst one ('ok' when empty)."""
    seen = {str(s) for s in statuses}
    for status in HEALTH_ORDER:
        if status in seen:
            return status
    return "unknown" if seen else "ok"
