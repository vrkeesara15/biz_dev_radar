"""Registration / certificate expiry windows and the bid block (SPEC 4.1, 7, 9). Pure.

    window_for(date(2026, 12, 1), today=date(2026, 10, 2))   -> 60
    is_expired(date(2026, 9, 1), today=date(2026, 10, 2))    -> True
    blocks_bidding("sam")                                     -> True

SPEC 7 sends the tenant owner a reminder 60, 30 and 7 days before a SAM registration, a
DSC, a certification or an insurance policy expires, and SPEC 4.1 says an expired SAM
registration "blocks bids". The daily job runs once a day, so a window is "hit" for the
whole day it falls on rather than at an exact instant — and a job that missed a day still
catches the window, because the check is `days_left <= offset` against the largest offset
not already notified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# SPEC 7: "Registration expiring (SAM, DSC, certifications, insurance) ... 60/30/7 days"
EXPIRY_OFFSETS: tuple[int, ...] = (60, 30, 7)

KIND_REGISTRATION = "registration"
KIND_CERTIFICATION = "certification"
KIND_INSURANCE = "insurance"
ITEM_KINDS: tuple[str, ...] = (KIND_REGISTRATION, KIND_CERTIFICATION, KIND_INSURANCE)

# SPEC 4.1: an expired SAM registration blocks US bids; an expired DSC blocks Indian ones
BLOCKING_REGISTRATIONS: tuple[str, ...] = ("sam", "dsc")

BLOCKED_REASON = (
    "this profile is blocked for bids: its {kinds} registration has expired "
    "(SPEC 4.1). Renew it and clear the block in Settings."
)


def blocks_bidding(registration_kind: str) -> bool:
    return str(registration_kind).lower() in BLOCKING_REGISTRATIONS


def days_left(expires_on: date, today: date) -> int:
    return (expires_on - today).days


def is_expired(expires_on: date | None, today: date) -> bool:
    """Expiring TODAY is not yet expired: the credential is valid through its last day."""
    return expires_on is not None and expires_on < today


def window_for(
    expires_on: date | None, today: date, *, already_sent: int | None = None
) -> int | None:
    """Which 60/30/7 window this expiry is inside, or None when no reminder is due.

    The TIGHTEST window that applies is reported, so a job that skipped a few days still
    says "7 days" rather than "30". `already_sent` is the last window the tenant was told
    about, so each window produces one reminder instead of one a day for sixty days.
    """
    if expires_on is None:
        return None
    remaining = days_left(expires_on, today)
    if remaining < 0:
        return None  # expired: that is a block, not a reminder
    hit = next((offset for offset in reversed(EXPIRY_OFFSETS) if remaining <= offset), None)
    if hit is None or (already_sent is not None and hit >= already_sent):
        return None
    return hit


def blocked_reason(kinds: list[str]) -> str:
    return BLOCKED_REASON.format(kinds=" and ".join(sorted({k.upper() for k in kinds})))


@dataclass(frozen=True, slots=True)
class ExpiringItem:
    """One thing with an expiry date, flattened out of its table for the daily job."""

    kind: str  # registration | certification | insurance
    subkind: str  # sam | dsc | 8a | general_liability | ...
    label: str
    expires_on: date
    profile_id: str
    item_id: str

    def as_payload(self, window: int, today: date) -> dict[str, object]:
        return {
            "kind": self.kind,
            "subkind": self.subkind,
            "label": self.label,
            "expires_on": self.expires_on.isoformat(),
            "days_left": days_left(self.expires_on, today),
            "window": window,
            "profile_id": self.profile_id,
            "item_id": self.item_id,
            "blocks_bids": self.kind == KIND_REGISTRATION and blocks_bidding(self.subkind),
        }
