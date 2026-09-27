"""Key dates auto-created per pursuit (SPEC 9). Pure, no I/O.

    dates = auto_dates(NoticeDates(response_due_at=due, source_tz="America/New_York"), "us", now)
    [(d.kind, d.at) for d in dates]
        -> [("internal_draft", due - 5d), ("internal_review", due - 3d),
            ("internal_final", due - 48h), ("portal_submission", due)]

SPEC 9 lists: questions due, pre-bid meeting, internal draft complete (due - 5 days),
internal review (due - 3 days), internal final (due - 48 h), portal submission due; India
adds EMD/BG ready and the DSC check.

Questions due and the pre-bid meeting are the buyer's own dates and are copied from the
notice when it states them — never invented. Everything else is derived from the response
deadline, so a notice with no deadline (a forecast, a sources-sought) yields no dates at
all rather than a ladder hanging off nothing.

`now` never changes WHICH dates exist — a milestone whose moment has passed is still
returned, flagged `is_past`, so the reminder ladder can say "overdue" and an amendment
that pushes the deadline out makes the same row future again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

KIND_QUESTIONS_DUE = "questions_due"
KIND_PREBID_MEETING = "prebid_meeting"
KIND_INTERNAL_DRAFT = "internal_draft"
KIND_INTERNAL_REVIEW = "internal_review"
KIND_INTERNAL_FINAL = "internal_final"
KIND_PORTAL_SUBMISSION = "portal_submission"
KIND_EMD_BG_READY = "emd_bg_ready"
KIND_DSC_CHECK = "dsc_check"
KIND_CUSTOM = "custom"

# Every kind the system creates by itself: at most one per pursuit (the DB enforces it).
AUTO_KINDS: tuple[str, ...] = (
    KIND_QUESTIONS_DUE,
    KIND_PREBID_MEETING,
    KIND_INTERNAL_DRAFT,
    KIND_INTERNAL_REVIEW,
    KIND_INTERNAL_FINAL,
    KIND_PORTAL_SUBMISSION,
    KIND_EMD_BG_READY,
    KIND_DSC_CHECK,
)
KINDS: tuple[str, ...] = (*AUTO_KINDS, KIND_CUSTOM)

SOURCE_AUTO = "auto"
SOURCE_USER = "user"
SOURCES: tuple[str, ...] = (SOURCE_AUTO, SOURCE_USER)

LABELS: dict[str, str] = {
    KIND_QUESTIONS_DUE: "Questions due",
    KIND_PREBID_MEETING: "Pre-bid meeting",
    KIND_INTERNAL_DRAFT: "Internal draft complete",
    KIND_INTERNAL_REVIEW: "Internal review",
    KIND_INTERNAL_FINAL: "Internal final",
    KIND_PORTAL_SUBMISSION: "Portal submission due",
    KIND_EMD_BG_READY: "EMD / bank guarantee ready",
    KIND_DSC_CHECK: "DSC check",
    KIND_CUSTOM: "Key date",
}

# kind -> hours before the response deadline (SPEC 9)
DEADLINE_OFFSET_HOURS: dict[str, int] = {
    KIND_INTERNAL_DRAFT: 5 * 24,
    KIND_INTERNAL_REVIEW: 3 * 24,
    KIND_INTERNAL_FINAL: 48,
    KIND_PORTAL_SUBMISSION: 0,
    # India: the EMD / bank guarantee takes days at a bank, the DSC is a same-week check
    KIND_EMD_BG_READY: 5 * 24,
    KIND_DSC_CHECK: 3 * 24,
}

US_DERIVED: tuple[str, ...] = (
    KIND_INTERNAL_DRAFT,
    KIND_INTERNAL_REVIEW,
    KIND_INTERNAL_FINAL,
    KIND_PORTAL_SUBMISSION,
)
IN_DERIVED: tuple[str, ...] = (
    KIND_EMD_BG_READY,
    KIND_INTERNAL_DRAFT,
    KIND_INTERNAL_REVIEW,
    KIND_DSC_CHECK,
    KIND_INTERNAL_FINAL,
    KIND_PORTAL_SUBMISSION,
)


def label_for(kind: str) -> str:
    return LABELS.get(kind, LABELS[KIND_CUSTOM])


def derived_kinds(region: str) -> tuple[str, ...]:
    """Which deadline-derived kinds a region gets (India adds EMD/BG and the DSC check)."""
    return IN_DERIVED if str(region).lower() == "in" else US_DERIVED


@dataclass(frozen=True, slots=True)
class NoticeDates:
    """The only things the rules read off an opportunity (plain values, not the ORM row)."""

    response_due_at: datetime | None = None
    questions_due_at: datetime | None = None
    prebid_meeting_at: datetime | None = None
    source_tz: str = "UTC"


@dataclass(frozen=True, slots=True)
class KeyDate:
    kind: str
    at: datetime
    label: str
    buyer_tz: str
    # "response deadline", "5 days before the deadline", ... shown next to the row
    note: str
    is_past: bool = False


def _note(hours: int) -> str:
    if hours == 0:
        return "the response deadline"
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''} before the deadline"
    return f"{hours} hours before the deadline"


def auto_dates(notice: NoticeDates, region: str, now: datetime) -> list[KeyDate]:
    """Every date SPEC 9 auto-creates for one pursuit, soonest first.

    `now` only decides `is_past`; the set of dates depends on the notice and the region.
    """
    out: list[KeyDate] = []
    if notice.questions_due_at is not None:
        out.append(
            _make(KIND_QUESTIONS_DUE, notice.questions_due_at, notice, now, "stated by the buyer")
        )
    if notice.prebid_meeting_at is not None:
        out.append(
            _make(KIND_PREBID_MEETING, notice.prebid_meeting_at, notice, now, "stated by the buyer")
        )
    due = notice.response_due_at
    if due is not None:
        for kind in derived_kinds(region):
            hours = DEADLINE_OFFSET_HOURS[kind]
            out.append(_make(kind, due - timedelta(hours=hours), notice, now, _note(hours)))
    return sorted(out, key=lambda d: (d.at, d.kind))


def _make(kind: str, at: datetime, notice: NoticeDates, now: datetime, note: str) -> KeyDate:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError(f"{kind} must be an aware datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return KeyDate(
        kind=kind,
        at=at,
        label=label_for(kind),
        buyer_tz=notice.source_tz,
        note=note,
        is_past=at <= now,
    )
