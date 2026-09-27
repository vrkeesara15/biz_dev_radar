"""USAspending.gov adapter (SPEC 2 row 3, 5.2): weekly, POST /api/v2/search/spending_by_award/
paged 100 at a time, capped at 50,000 results per query. No key.

`fetch(since, cursor)` returns contract awards whose action dates fall in the last three
fiscal years (the statistics window); `since` only narrows that window when it is later.
Records carry the `AwardRecord` in `raw.meta["award"]` for the spend job; `normalize()`
yields the contract-conformant award notice.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import structlog

from app.adapters.base import (
    AdapterHealth,
    AdapterStatus,
    DocumentRef,
    OpportunityIn,
    RawRecord,
)
from app.adapters.http import PoliteClient
from app.adapters.registry import register
from app.core.config import Settings, get_settings
from app.core.normalize.usaspending import (
    DOD_LAG_NOTE,
    SOURCE_ID,
    award_from_row,
    award_to_opportunity,
    fiscal_year_start,
    last_fiscal_years,
    spending_by_award_body,
)

log = structlog.get_logger(__name__)

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
PAGE_SIZE = 100
MAX_RESULTS = 50_000


class UsaSpendingApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"USAspending API returned HTTP {status}: {detail}")
        self.status = status


@register
class UsaSpendingAdapter:
    source_id = SOURCE_ID
    region = "us"
    schedule = "0 3 * * 1"  # weekly, Monday 03:00

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        page_size: int = PAGE_SIZE,
        max_results: int = MAX_RESULTS,
        naics_codes: list[str] | None = None,
        fiscal_years: int = 3,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.page_size = min(page_size, PAGE_SIZE)
        self.max_results = max_results
        self.naics_codes = naics_codes
        self.fiscal_years = fiscal_years
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(settings=self.settings)
        return self._client

    def _post(self, body: dict[str, Any], archive_id: str) -> tuple[dict[str, Any], Any]:
        response = self.client.post(
            SEARCH_URL,
            json=body,
            headers={"Content-Type": "application/json"},
            source_id=self.source_id,
            external_id=archive_id,
        )
        if response.status_code != 200:
            raise UsaSpendingApiError(response.status_code, response.text[:200])
        try:
            payload = response.json()
        except ValueError as exc:
            raise UsaSpendingApiError(200, "response is not JSON") from exc
        if not isinstance(payload, dict):
            raise UsaSpendingApiError(200, "unexpected body")
        return payload, response

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        now = self._now()
        self._last_run_at = now
        years = last_fiscal_years(now, self.fiscal_years)
        start = (
            max(fiscal_year_start(years[0]), since.date()) if since else fiscal_year_start(years[0])
        )
        start = min(start, now.date())
        end = now.date()
        page = 1
        if cursor:
            try:
                page = max(1, int(json.loads(cursor).get("page", 1)))
            except (ValueError, AttributeError):
                page = 1
        try:
            while True:
                body = spending_by_award_body(
                    start, end, page=page, limit=self.page_size, naics_codes=self.naics_codes
                )
                payload, response = self._post(body, archive_id=f"spending_by_award/{page}")
                results = payload.get("results") or []
                meta = payload.get("page_metadata") or {}
                has_next = bool(meta.get("hasNext")) and bool(results)
                reached_cap = page * self.page_size >= self.max_results
                for position, row in enumerate(results):
                    award = award_from_row(row)
                    last = position == len(results) - 1
                    yield RawRecord(
                        source_id=self.source_id,
                        external_id=award.generated_internal_id or award.award_id,
                        fetched_at=response.fetched_at,
                        payload=row,
                        raw_ref=response.raw_ref,
                        meta={
                            "award": award,
                            "cursor": json.dumps({"page": page + 1 if last else page}),
                            "page": page,
                            "messages": payload.get("messages") or [],
                        },
                    )
                if not has_next or reached_cap:
                    if has_next and reached_cap:
                        log.warning("usaspending.result_cap", cap=self.max_results, page=page)
                    break
                page += 1
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def fetch_detail(self, external_id: str) -> RawRecord:
        raise NotImplementedError("USAspending awards are complete in the search response")

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        return []

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        award = raw.meta.get("award")
        if award is None:
            if not isinstance(raw.payload, dict):
                raise ValueError("USAspending records are JSON objects")
            award = award_from_row(raw.payload)
        return award_to_opportunity(award)

    def health(self) -> AdapterHealth:
        if self._last_error:
            return AdapterHealth(
                AdapterStatus.FAILING, self._last_run_at, f"{self._last_error}; {DOD_LAG_NOTE}"
            )
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, DOD_LAG_NOTE)
