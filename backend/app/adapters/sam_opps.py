"""SAM.gov Get Opportunities v2 adapter (SPEC 2 row 1, 5.2).

Polls every 30 minutes with postedFrom = watermark - 2 days (the runner passes `since`),
limit=1000 and offset pagination until totalRecords. Windows never exceed one year.

Cursor (JSON) = {"from": MM/dd/yyyy, "to": MM/dd/yyyy, "offset": n}: every record of a page
carries the page's own offset; the LAST record of a page carries the next page's offset, so a
run aborted by RetryExhaustedError resumes at the first page it did not finish.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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
from app.core.normalize.sam import (
    SOURCE_ID,
    map_resource_links,
    normalize_sam_notice,
    parse_sam_datetime,
    sam_window_params,
)
from app.core.watermark import MAX_WINDOW, Window, bounded_windows

log = structlog.get_logger(__name__)

SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"
PAGE_SIZE = 1000


class SamApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"SAM.gov API returned HTTP {status}: {detail}")
        self.status = status


def encode_cursor(window_from: str, window_to: str, offset: int) -> str:
    return json.dumps({"from": window_from, "to": window_to, "offset": offset}, sort_keys=True)


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        data = json.loads(cursor)
    except ValueError:
        return None
    if not isinstance(data, dict) or not {"from", "to", "offset"} <= set(data):
        return None
    return data


@register
class SamOpportunitiesAdapter:
    source_id = SOURCE_ID
    region = "us"
    schedule = "*/30 * * * *"

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.page_size = min(page_size, PAGE_SIZE)
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(settings=self.settings)
        return self._client

    # -- fetch ------------------------------------------------------------------------
    def _windows(self, since: datetime, cursor: dict[str, Any] | None) -> list[Window]:
        now = self._now()
        if cursor is None:
            return bounded_windows(since, now, max_window=MAX_WINDOW)
        # Resume the interrupted window first, then everything after it. postedTo is a
        # whole (inclusive) day, so the tail starts on the following SAM day.
        start = parse_sam_datetime(cursor["from"])
        end = parse_sam_datetime(cursor["to"])
        assert start is not None and end is not None
        windows = [Window(start, end)]
        tail_start = end + timedelta(days=1)
        if tail_start < now:
            windows.extend(bounded_windows(tail_start, now, max_window=MAX_WINDOW))
        return windows

    def _search(self, params: dict[str, Any], archive_id: str) -> dict[str, Any]:
        response = self.client.get(
            SEARCH_URL,
            params={"api_key": self.settings.sam_api_key, **params},
            source_id=self.source_id,
            external_id=archive_id,
        )
        if response.status_code != 200:
            raise SamApiError(response.status_code, response.text[:200])
        data = response.json()
        if not isinstance(data, dict):
            raise SamApiError(200, "unexpected body")
        data["_raw_ref"] = response.raw_ref
        data["_fetched_at"] = response.fetched_at
        return data

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        self._last_run_at = self._now()
        if not self.settings.sam_api_key:
            self._last_error = "SAM_API_KEY is not configured"
            log.warning("sam_opps.no_api_key")
            return
        resume = decode_cursor(cursor)
        try:
            for index, window in enumerate(self._windows(since, resume)):
                if window.length.total_seconds() <= 0:
                    continue
                window_params = sam_window_params(window.start, window.end)
                offset = int(resume["offset"]) if resume is not None and index == 0 else 0
                yield from self._fetch_window(window_params, offset)
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def _fetch_window(self, window_params: dict[str, str], offset: int) -> Iterator[RawRecord]:
        posted_from, posted_to = window_params["postedFrom"], window_params["postedTo"]
        while True:
            data = self._search(
                {**window_params, "limit": self.page_size, "offset": offset},
                archive_id=f"search/{posted_from.replace('/', '-')}/{offset}",
            )
            items = data.get("opportunitiesData") or []
            total = int(data.get("totalRecords") or 0)
            next_offset = offset + len(items)
            done = not items or next_offset >= total
            for position, item in enumerate(items):
                last = position == len(items) - 1
                yield RawRecord(
                    source_id=self.source_id,
                    external_id=str(item.get("noticeId") or ""),
                    fetched_at=data["_fetched_at"],
                    payload=item,
                    raw_ref=data["_raw_ref"],
                    meta={
                        "cursor": encode_cursor(
                            posted_from, posted_to, next_offset if last else offset
                        ),
                        "page_offset": offset,
                        "total_records": total,
                    },
                )
            if done:
                return
            offset = next_offset

    # -- detail / documents / normalize ---------------------------------------------------
    def fetch_detail(self, external_id: str) -> RawRecord:
        data = self._search({"noticeid": external_id, "limit": 1}, archive_id=external_id)
        items = data.get("opportunitiesData") or []
        if not items:
            raise LookupError(f"SAM.gov notice {external_id} not found")
        return RawRecord(
            source_id=self.source_id,
            external_id=external_id,
            fetched_at=data["_fetched_at"],
            payload=items[0],
            raw_ref=data["_raw_ref"],
        )

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        payload = raw.payload if isinstance(raw.payload, dict) else {}
        return map_resource_links(payload)

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        if not isinstance(raw.payload, dict):
            raise ValueError("SAM.gov records are JSON objects")
        return normalize_sam_notice(raw.payload, description_text=raw.meta.get("description_text"))

    # -- health -------------------------------------------------------------------------
    def health(self) -> AdapterHealth:
        if not self.settings.sam_api_key:
            return AdapterHealth(
                AdapterStatus.DEGRADED, self._last_run_at, "SAM_API_KEY is not configured"
            )
        if self._last_error:
            return AdapterHealth(AdapterStatus.FAILING, self._last_run_at, self._last_error)
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, None)
