"""Adapter contract (SPEC 5.1).

Every source is a class implementing `SourceAdapter`. Adapters are plain, synchronous
objects: `fetch()` is a generator of `RawRecord`s, `normalize()` maps one raw record to
the canonical `OpportunityIn`. All network I/O goes through `app.adapters.http`
(rate limits, backoff, robots.txt, raw archive); the pipeline (`app.services`) owns
the database and wraps adapters in the async runner.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from app.core.opportunity import (
    Contact,
    DetailStatus,
    DocumentKind,
    DocumentRef,
    NoticeType,
    OpportunityIn,
    OpportunityStatus,
    PlaceOfPerformance,
)

__all__ = [
    "AdapterHealth",
    "AdapterStatus",
    "Contact",
    "DegradedNotes",
    "DetailStatus",
    "DocumentKind",
    "DocumentRef",
    "NoticeType",
    "OpportunityIn",
    "OpportunityStatus",
    "PlaceOfPerformance",
    "RawRecord",
    "SourceAdapter",
]

RegionCode = Literal["us", "in"]


@dataclass(slots=True)
class RawRecord:
    """One source record exactly as fetched, plus where its body was archived."""

    source_id: str
    external_id: str
    fetched_at: datetime
    payload: dict[str, Any] | str
    content_type: str = "application/json"
    # Object-storage key of the archived body:
    #   raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at}
    raw_ref: str | None = None
    # Free-form context an adapter wants to carry from fetch() to normalize() (e.g. the
    # search hit that pointed at this detail record).
    meta: dict[str, Any] = field(default_factory=dict)


class AdapterStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILING = "failing"
    NOT_IMPLEMENTED = "not_implemented"
    ROBOTS_DISALLOWED = "robots_disallowed"


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    status: AdapterStatus
    last_run_at: datetime | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is AdapterStatus.OK


class DegradedNotes:
    """Bounded, de-duplicated list of 'page looked wrong' notes an adapter collects during
    fetch(); non-empty notes turn health() DEGRADED (SPEC 5.1 layout-change detection)."""

    MAX = 10

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, note: str) -> None:
        if note not in self.items and len(self.items) < self.MAX:
            self.items.append(note)

    def reset(self) -> None:
        self.items.clear()

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def message(self) -> str:
        return "; ".join(self.items)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@runtime_checkable
class SourceAdapter(Protocol):
    """SPEC 5.1, verbatim. Class attributes identify the source; methods are sync."""

    source_id: str  # 'sam_opps', 'sam_awards', 'usaspending', 'grants_gov', 'cppp', ...
    region: RegionCode
    schedule: str  # cron, e.g. '*/30 * * * *'

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]: ...

    def fetch_detail(self, external_id: str) -> RawRecord: ...

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]: ...

    def normalize(self, raw: RawRecord) -> OpportunityIn: ...

    def health(self) -> AdapterHealth: ...
