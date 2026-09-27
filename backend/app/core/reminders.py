"""The reminder ladder and its escalation (SPEC 9). Pure, no I/O.

    ladder(due, now)
        -> [("7d", due - 7d), ("3d", due - 3d), ("24h", due - 24h),
            ("4h", due - 4h), ("1h", due - 1h)]
    overdue_slots(due, now)      -> [("overdue", due + 4h), ("overdue", due + 8h), ...]
    escalation_level("4h", acknowledged=False)   -> 2

SPEC 9: "7 days, 3 days, 24 h, 4 h, 1 h before, and overdue every 4 h. Escalation: if not
acknowledged by the owner 24 h before due, notify the bid manager; at 4 h, notify the
tenant owner."

Level 0 reaches the pursuit owner, level 1 adds the tenant's bid managers, level 2 adds
the tenant owner. Acknowledging the key date drops every later rung back to level 0 — the
point of the escalation is that nobody has looked, not that the deadline is close.

Everything is computed on absolute instants (UTC). Rendering in the reader's zone is
`core.display_time`'s job, so a US and an Indian reader of the SAME deadline are reminded
at the same moment and simply see different clocks — which is what "in the user's time
zone" means for a shared deadline.
"""

from __future__ import annotations

from datetime import datetime, timedelta

LABEL_7D = "7d"
LABEL_3D = "3d"
LABEL_24H = "24h"
LABEL_4H = "4h"
LABEL_1H = "1h"
LABEL_OVERDUE = "overdue"

# label -> how long before the date it fires, in SPEC 9 order
LADDER_OFFSETS: tuple[tuple[str, timedelta], ...] = (
    (LABEL_7D, timedelta(days=7)),
    (LABEL_3D, timedelta(days=3)),
    (LABEL_24H, timedelta(hours=24)),
    (LABEL_4H, timedelta(hours=4)),
    (LABEL_1H, timedelta(hours=1)),
)
LABELS: tuple[str, ...] = (*(label for label, _ in LADDER_OFFSETS), LABEL_OVERDUE)

OVERDUE_EVERY = timedelta(hours=4)
# how many overdue rungs one pass may add, so a date that went past months ago (an import,
# a clock jump) produces a handful of reminders rather than hundreds
MAX_OVERDUE_PER_PASS = 3

ESCALATION_OWNER = 0  # the pursuit owner
ESCALATION_MANAGER = 1  # + the tenant's bid managers
ESCALATION_TENANT_OWNER = 2  # + the tenant owner

# SPEC 9: unacknowledged at 24 h -> the bid manager; at 4 h -> the tenant owner. Once the
# tenant owner is involved they stay involved for the last rungs.
ESCALATION_BY_LABEL: dict[str, int] = {
    LABEL_7D: ESCALATION_OWNER,
    LABEL_3D: ESCALATION_OWNER,
    LABEL_24H: ESCALATION_MANAGER,
    LABEL_4H: ESCALATION_TENANT_OWNER,
    LABEL_1H: ESCALATION_TENANT_OWNER,
    LABEL_OVERDUE: ESCALATION_TENANT_OWNER,
}


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


def is_label(value: str) -> bool:
    return value in LABELS


def ladder(due_at: datetime, now: datetime | None = None) -> list[tuple[str, datetime]]:
    """The five pre-deadline rungs, earliest first.

    With `now`, rungs that are already behind us are dropped: a notice opened two days
    before its deadline is not spammed with a "7 days to go" reminder. Without `now`,
    every rung is returned (what a test or a re-derivation after an amendment wants).
    """
    _aware(due_at, "due_at")
    rungs = [(label, due_at - offset) for label, offset in LADDER_OFFSETS]
    if now is None:
        return rungs
    moment = _aware(now, "now")
    return [rung for rung in rungs if rung[1] > moment]


def overdue_slots(
    due_at: datetime, now: datetime, *, after: datetime | None = None, limit: int | None = None
) -> list[tuple[str, datetime]]:
    """Overdue rungs every 4 h from the deadline up to `now` (SPEC 9).

    `after` is the last overdue rung already recorded, so a beat tick only ever adds the
    ones that have come due since. At most `limit` (default MAX_OVERDUE_PER_PASS) per call.
    """
    _aware(due_at, "due_at")
    moment = _aware(now, "now")
    if moment <= due_at:
        return []
    start = _aware(after, "after") if after is not None else due_at
    cap = MAX_OVERDUE_PER_PASS if limit is None else max(0, limit)
    out: list[tuple[str, datetime]] = []
    slot = max(start, due_at) + OVERDUE_EVERY
    while slot <= moment and len(out) < cap:
        out.append((LABEL_OVERDUE, slot))
        slot += OVERDUE_EVERY
    return out


def escalation_level(label: str, *, acknowledged: bool = False) -> int:
    """Who this rung reaches. An acknowledged date never escalates (SPEC 9)."""
    if acknowledged:
        return ESCALATION_OWNER
    return ESCALATION_BY_LABEL.get(label, ESCALATION_OWNER)


def label_order(label: str) -> int:
    """Sort key: the ladder in firing order, with `overdue` last."""
    return LABELS.index(label) if label in LABELS else len(LABELS)


def describe(label: str) -> str:
    """The human phrase used in the notification subject."""
    return {
        LABEL_7D: "7 days to go",
        LABEL_3D: "3 days to go",
        LABEL_24H: "24 hours to go",
        LABEL_4H: "4 hours to go",
        LABEL_1H: "1 hour to go",
        LABEL_OVERDUE: "overdue",
    }.get(label, label)
