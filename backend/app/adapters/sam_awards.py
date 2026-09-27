"""SAM.gov contract awards adapter (SPEC 2 row 2, 5.2): daily, by NAICS list, SAM API key.

Endpoint and NAICS list come from settings (SAM_AWARDS_API_URL, SAM_AWARDS_NAICS) or the
constructor; the response is read through `core.normalize.sam_awards.ALIASES` so a field
rename on the live service is a data change. Windows are by signed date, <= 1 year each,
paginated by limit/offset like the opportunities API.
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
from app.core.normalize.sam_awards import (
    SOURCE_ID,
    award_from_record,
    award_search_params,
    award_to_opportunity,
)
from app.core.watermark import MAX_WINDOW, bounded_windows

log = structlog.get_logger(__name__)

PAGE_SIZE = 100
RESULT_KEYS = ("awardsData", "awardData", "contractAwards", "results", "data")


class SamAwardsApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"SAM.gov awards API returned HTTP {status}: {detail}")
        self.status = status


@register
class SamAwardsAdapter:
    source_id = SOURCE_ID
    region = "us"
    schedule = "0 4 * * *"  # daily

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        naics_codes: list[str] | None = None,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.naics_codes = [c for c in (naics_codes or self.settings.sam_awards_naics) if c]
        self.page_size = page_size
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(settings=self.settings)
        return self._client

    def _search(self, params: dict[str, Any], archive_id: str) -> tuple[dict[str, Any], Any]:
        response = self.client.get(
            self.settings.sam_awards_api_url,
            params={"api_key": self.settings.sam_api_key, **params},
            source_id=self.source_id,
            external_id=archive_id,
        )
        if response.status_code != 200:
            raise SamAwardsApiError(response.status_code, response.text[:200])
        payload = response.json()
        if not isinstance(payload, dict):
            raise SamAwardsApiError(200, "unexpected body")
        return payload, response

    @staticmethod
    def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in RESULT_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    def _blocked(self) -> str | None:
        if not self.settings.sam_api_key:
            return "SAM_API_KEY is not configured"
        if not self.naics_codes:
            return "no NAICS codes configured for the awards search (SAM_AWARDS_NAICS)"
        return None

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        now = self._now()
        self._last_run_at = now
        blocked = self._blocked()
        if blocked:
            self._last_error = blocked
            log.warning("sam_awards.blocked", reason=blocked)
            return
        start_offset = 0
        if cursor:
            try:
                start_offset = int(json.loads(cursor).get("offset", 0))
            except (ValueError, AttributeError):
                start_offset = 0
        try:
            for index, window in enumerate(bounded_windows(since, now, max_window=MAX_WINDOW)):
                if window.length.total_seconds() <= 0:
                    continue
                offset = start_offset if index == 0 else 0
                while True:
                    params = award_search_params(
                        self.naics_codes,
                        window.start,
                        window.end,
                        limit=self.page_size,
                        offset=offset,
                    )
                    payload, response = self._search(
                        params,
                        archive_id=f"search/{params['signedDateFrom'].replace('/', '-')}/{offset}",
                    )
                    rows = self._rows(payload)
                    total = int(payload.get("totalRecords") or 0)
                    next_offset = offset + len(rows)
                    done = not rows or next_offset >= total
                    for position, row in enumerate(rows):
                        award = award_from_record(row)
                        last = position == len(rows) - 1
                        yield RawRecord(
                            source_id=self.source_id,
                            external_id=award.award_id,
                            fetched_at=response.fetched_at,
                            payload=row,
                            raw_ref=response.raw_ref,
                            meta={
                                "award": award,
                                "cursor": json.dumps({"offset": next_offset if last else offset}),
                            },
                        )
                    if done:
                        break
                    offset = next_offset
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def fetch_detail(self, external_id: str) -> RawRecord:
        raise NotImplementedError("awards are complete in the search response")

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        return []

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        award = raw.meta.get("award")
        if award is None:
            if not isinstance(raw.payload, dict):
                raise ValueError("award records are JSON objects")
            award = award_from_record(raw.payload)
        return award_to_opportunity(award)

    def health(self) -> AdapterHealth:
        blocked = self._blocked()
        if blocked:
            return AdapterHealth(AdapterStatus.DEGRADED, self._last_run_at, blocked)
        if self._last_error:
            return AdapterHealth(AdapterStatus.FAILING, self._last_run_at, self._last_error)
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, None)
