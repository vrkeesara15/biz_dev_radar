"""Minimal in-memory adapter used to prove the pipeline end-to-end (M2-01)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from app.adapters.base import (
    AdapterHealth,
    AdapterStatus,
    DocumentRef,
    NoticeType,
    OpportunityIn,
    RawRecord,
)
from app.core.config import Region


def raw_record(external_id: str, **payload: Any) -> RawRecord:
    return RawRecord(
        source_id=FixtureAdapter.source_id,
        external_id=external_id,
        fetched_at=datetime.now(UTC),
        payload={"id": external_id, **payload},
    )


class FixtureAdapter:
    """Yields the records it was constructed with; `bad_ids` fail to normalise."""

    source_id = "fixture"
    region = "us"
    schedule = "*/30 * * * *"

    def __init__(
        self,
        records: list[RawRecord] | None = None,
        *,
        bad_ids: set[str] | None = None,
        fail_fetch: Exception | None = None,
    ) -> None:
        self.records = records or []
        self.bad_ids = bad_ids or set()
        self.fail_fetch = fail_fetch
        self.calls: list[tuple[datetime, str | None]] = []

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        self.calls.append((since, cursor))
        if self.fail_fetch is not None:
            raise self.fail_fetch
        for record in self.records:
            posted = record.payload["posted_at"] if isinstance(record.payload, dict) else None
            if posted is None or posted >= since:
                yield record

    def fetch_detail(self, external_id: str) -> RawRecord:
        for record in self.records:
            if record.external_id == external_id:
                return record
        raise KeyError(external_id)

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        assert isinstance(raw.payload, dict)
        return [
            DocumentRef(url=u, file_name=u.rsplit("/", 1)[-1]) for u in raw.payload.get("docs", [])
        ]

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        assert isinstance(raw.payload, dict)
        if raw.external_id in self.bad_ids:
            raise ValueError(f"cannot normalise {raw.external_id}")
        p = raw.payload
        return OpportunityIn(
            source_id=self.source_id,
            external_id=raw.external_id,
            source_url=f"https://example.test/notice/{raw.external_id}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType(p.get("notice_type", "rfp")),
            title=p.get("title", f"Notice {raw.external_id}"),
            description_text=p.get("description"),
            solicitation_number=p.get("solicitation_number"),
            buyer_org=p.get("buyer_org", "Test Agency"),
            naics=p.get("naics", ["541512"]),
            posted_at=p.get("posted_at"),
            response_due_at=p.get("response_due_at"),
            documents=self.fetch_documents(raw),
            source_tz="America/New_York",
        )

    def health(self) -> AdapterHealth:
        return AdapterHealth(status=AdapterStatus.OK, message="fixture")
