"""Grants.gov adapter (SPEC 2 row 4, 5.2): search2 every 2 h (posted + forecasted), then
fetchOpportunity per kept hit for eligibility, award ceiling/floor, close date, ALN codes.

No key, no quota. search2 has no "posted since" filter beyond its dateRange buckets
(3..56 days), so `since` picks the smallest bucket that covers it; anything older lists
everything posted/forecasted (bounded, a few thousand rows).
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
from app.core.normalize.grants import (
    SOURCE_ID,
    date_range_for,
    keep_latest,
    map_documents,
    normalize_grants,
)

log = structlog.get_logger(__name__)

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
PAGE_SIZE = 100
OPP_STATUSES = "forecasted|posted"


class GrantsApiError(RuntimeError):
    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(f"Grants.gov API error: {detail}")
        self.status = status


def encode_cursor(start: int, date_range: str | None) -> str:
    return json.dumps({"start": start, "dateRange": date_range}, sort_keys=True)


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        data = json.loads(cursor)
    except ValueError:
        return None
    return data if isinstance(data, dict) and "start" in data else None


@register
class GrantsGovAdapter:
    source_id = SOURCE_ID
    region = "us"
    schedule = "0 */2 * * *"

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        page_size: int = PAGE_SIZE,
        with_details: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.page_size = page_size
        self.with_details = with_details
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(settings=self.settings)
        return self._client

    # -- HTTP -------------------------------------------------------------------------------
    def _post(self, url: str, body: dict[str, Any], archive_id: str) -> tuple[dict[str, Any], Any]:
        response = self.client.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            source_id=self.source_id,
            external_id=archive_id,
        )
        if response.status_code != 200:
            raise GrantsApiError(f"HTTP {response.status_code}", status=response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GrantsApiError("response is not JSON") from exc
        if not isinstance(payload, dict):
            raise GrantsApiError("unexpected body")
        if int(payload.get("errorcode") or 0) != 0:
            raise GrantsApiError(str(payload.get("msg") or payload.get("errorcode")))
        return payload, response

    # -- fetch -----------------------------------------------------------------------------
    def _search_pages(
        self, start: int, date_range: str | None
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        while True:
            body: dict[str, Any] = {
                "rows": self.page_size,
                "startRecordNum": start,
                "oppStatuses": OPP_STATUSES,
                "sortBy": "openDate|desc",
            }
            if date_range:
                body["dateRange"] = date_range
            payload, _ = self._post(SEARCH_URL, body, archive_id=f"search2/{start}")
            data = payload.get("data") or {}
            hits = data.get("oppHits") or []
            total = int(data.get("hitCount") or 0)
            for hit in hits:
                yield start, hit
            start += len(hits)
            if not hits or start >= total:
                return

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        self._last_run_at = self._now()
        resume = decode_cursor(cursor)
        days = (self._now() - since).total_seconds() / 86_400
        date_range = resume.get("dateRange") if resume else date_range_for(days)
        start = int(resume["start"]) if resume else 0
        try:
            paged = list(self._search_pages(start, date_range))
            page_of = {str(hit.get("id")): page for page, hit in paged}
            kept = keep_latest(hit for _, hit in paged)
            for index, hit in enumerate(kept):
                detail = self._detail(str(hit.get("id"))) if self.with_details else None
                page_start = page_of.get(str(hit.get("id")), start)
                last = index == len(kept) - 1
                yield RawRecord(
                    source_id=self.source_id,
                    external_id=str(hit.get("id")),
                    fetched_at=self._now(),
                    payload={"hit": hit, "detail": detail},
                    raw_ref=detail.get("_raw_ref") if detail else None,
                    meta={
                        "cursor": encode_cursor(
                            page_start + self.page_size if last else page_start, date_range
                        ),
                        "date_range": date_range,
                    },
                )
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def _detail(self, opportunity_id: str) -> dict[str, Any]:
        payload, response = self._post(
            FETCH_URL, {"opportunityId": int(opportunity_id)}, archive_id=opportunity_id
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GrantsApiError(f"opportunity {opportunity_id} has no data")
        data["_raw_ref"] = response.raw_ref
        return data

    # -- detail / documents / normalize ----------------------------------------------------
    def fetch_detail(self, external_id: str) -> RawRecord:
        detail = self._detail(external_id)
        return RawRecord(
            source_id=self.source_id,
            external_id=external_id,
            fetched_at=self._now(),
            payload={"hit": None, "detail": detail},
            raw_ref=detail.get("_raw_ref"),
        )

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        payload = raw.payload if isinstance(raw.payload, dict) else {}
        return map_documents(payload.get("detail"))

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        if not isinstance(raw.payload, dict):
            raise ValueError("Grants.gov records are JSON objects")
        detail = raw.payload.get("detail")
        if detail is not None:
            detail = {k: v for k, v in detail.items() if not k.startswith("_")}
        return normalize_grants(raw.payload.get("hit"), detail)

    def health(self) -> AdapterHealth:
        if self._last_error:
            return AdapterHealth(AdapterStatus.FAILING, self._last_run_at, self._last_error)
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, None)
