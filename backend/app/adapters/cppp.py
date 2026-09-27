"""CPPP adapter (SPEC 2 row 7, 5.2; M3-02): eprocure.gov.in captcha-free listing pages.

Every 3 hours the adapter reads exactly two public pages, nothing else:

1. `latestactivetendersnew`  the 7-column "Latest Active Tenders" listing (verified capture)
2. `tendersbyorganisation`   the "Tenders by Organisation" page; when it is an organisation
                             index the first CPPP_MAX_ORGS_PER_RUN organisation listings
                             are followed (same 7-column table), when it lists tenders
                             directly it is parsed as such, and when it answers 404 or a
                             different layout the run is only DEGRADED (the primary page
                             still flows).

The search form, the archive and the per-tender detail need a CAPTCHA and are never
requested: each record carries `detail_status = manual`, the official `tendersfullview`
link as `source_url` and the portal search URL in `extra.manual_detail_url`. Requests go
through PoliteClient (1 req/s for *.gov.in, robots.txt honoured, raw pages archived).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import structlog

from app.adapters.base import (
    AdapterHealth,
    AdapterStatus,
    DegradedNotes,
    DocumentRef,
    OpportunityIn,
    RawRecord,
)
from app.adapters.http import PoliteClient
from app.adapters.registry import register
from app.core.config import Settings, get_settings
from app.core.normalize.cppp import (
    LATEST_ACTIVE_URL,
    PAGE_BY_ORG,
    PAGE_LATEST,
    SEARCH_URL,
    SOURCE_ID,
    external_id_for,
    normalize_cppp,
    row_to_payload,
)
from app.core.normalize.nicgep import (
    TenderRow,
    parse_listing_table,
    parse_org_index,
    parse_portal_datetime,
)

log = structlog.get_logger(__name__)


class CpppPortalError(RuntimeError):
    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"CPPP returned HTTP {status} for {url}")
        self.url = url
        self.status = status


@register
class CpppAdapter:
    source_id = SOURCE_ID
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "Central Public Procurement Portal (eprocure.gov.in)"

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        max_orgs: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.max_orgs = self.settings.cppp_max_orgs_per_run if max_orgs is None else max_orgs
        self.latest_url = LATEST_ACTIVE_URL
        self.by_org_url = self.settings.cppp_by_org_url
        self._notes = DegradedNotes()
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(settings=self.settings)
        return self._client

    # -- fetch ------------------------------------------------------------------------
    def _get(self, url: str, archive_id: str) -> tuple[str, str | None, datetime, int]:
        response = self.client.get(url, source_id=self.source_id, external_id=archive_id)
        return response.text, response.raw_ref, response.fetched_at, response.status_code

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        self._last_run_at = self._now()
        self._notes.reset()
        seen: set[str] = set()
        try:
            yield from self._latest(since, seen)
            yield from self._by_organisation(since, seen)
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def _records(
        self,
        rows: list[TenderRow],
        *,
        page: str,
        since: datetime,
        seen: set[str],
        raw_ref: str | None,
        fetched_at: datetime,
    ) -> Iterator[RawRecord]:
        for row in rows:
            payload = row_to_payload(row, page=page)
            published = parse_portal_datetime(row.published)
            if published is not None and published.utc < since:
                continue
            external_id = external_id_for(payload)
            if external_id in seen:
                continue
            seen.add(external_id)
            yield RawRecord(
                source_id=self.source_id,
                external_id=external_id,
                fetched_at=fetched_at,
                payload=payload,
                content_type="text/html",
                raw_ref=raw_ref,
                meta={"page": page},
            )

    def _latest(self, since: datetime, seen: set[str]) -> Iterator[RawRecord]:
        html, raw_ref, fetched_at, status = self._get(self.latest_url, "latestactivetendersnew")
        if status != 200:
            raise CpppPortalError(self.latest_url, status)
        outcome = parse_listing_table(html)
        for note in outcome.notes:
            self._notes.add(f"latest active tenders: {note}")
        yield from self._records(
            outcome.rows,
            page=PAGE_LATEST,
            since=since,
            seen=seen,
            raw_ref=raw_ref,
            fetched_at=fetched_at,
        )

    def _by_organisation(self, since: datetime, seen: set[str]) -> Iterator[RawRecord]:
        if not self.by_org_url:
            return
        html, raw_ref, fetched_at, status = self._get(self.by_org_url, "tendersbyorganisation")
        if status != 200:
            self._notes.add(f"tenders by organisation page unavailable (HTTP {status})")
            return
        listing = parse_listing_table(html)
        if listing.found:
            yield from self._records(
                listing.rows,
                page=PAGE_BY_ORG,
                since=since,
                seen=seen,
                raw_ref=raw_ref,
                fetched_at=fetched_at,
            )
            return
        index = parse_org_index(html)
        if not index.found:
            self._notes.add(
                "tenders by organisation: neither a tender listing nor an organisation "
                "index (layout change?)"
            )
            return
        followed = 0
        for org in index.rows:
            if followed >= self.max_orgs:
                break
            if not org.url or not org.count:
                continue
            followed += 1
            url = urljoin(self.by_org_url, org.url)
            org_html, org_ref, org_fetched, org_status = self._get(
                url, f"tendersbyorganisation/{followed}"
            )
            if org_status != 200:
                self._notes.add(f"organisation listing {org.name!r}: HTTP {org_status}")
                continue
            org_listing = parse_listing_table(org_html)
            for note in org_listing.notes:
                self._notes.add(f"organisation listing {org.name!r}: {note}")
            for row in org_listing.rows:
                if row.organisation is None:
                    row.organisation = org.name
            yield from self._records(
                org_listing.rows,
                page=PAGE_BY_ORG,
                since=since,
                seen=seen,
                raw_ref=org_ref,
                fetched_at=org_fetched,
            )

    # -- detail / documents / normalize -------------------------------------------------
    def fetch_detail(self, external_id: str) -> RawRecord:
        """CPPP detail pages sit behind a CAPTCHA (SPEC 2 row 7): no request is made; the
        record points a human at the portal search page."""
        return RawRecord(
            source_id=self.source_id,
            external_id=external_id,
            fetched_at=self._now(),
            payload={"detail_status": "manual", "manual_detail_url": SEARCH_URL},
            meta={"captcha": True},
        )

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        """Tender documents are only linked from the CAPTCHA-gated detail page."""
        return []

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        if not isinstance(raw.payload, dict):
            raise ValueError("CPPP records are parsed listing rows")
        payload: dict[str, Any] = raw.payload
        return normalize_cppp(payload, external_id=raw.external_id)

    # -- health -------------------------------------------------------------------------
    def health(self) -> AdapterHealth:
        if self._last_error:
            return AdapterHealth(AdapterStatus.FAILING, self._last_run_at, self._last_error)
        if self._notes:
            return AdapterHealth(AdapterStatus.DEGRADED, self._last_run_at, self._notes.message)
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, None)
