"""GeM adapter (SPEC 2 row 8, 5.2; M3-03): public "All Bids" listing + bid PDF download.

Every 3 hours: POST the listing endpoint (`GEM_BIDS_URL`, the JSON backend of
bidplus.gem.gov.in/all-bids) page by page for ongoing bids, newest bid-end first, until
the reported total, an empty page or `GEM_MAX_PAGES`. No login, no CAPTCHA, no seller
session: only what the public page itself loads. Each bid becomes a `gem_bid` /
`reverse_auction` notice with its bid document PDF as the single DocumentRef;
`fetch_documents` downloads that PDF through PoliteClient and fills sha256/size so the
pipeline can parse it (M3-04 extracts eligibility from it).

bidplus.gem.gov.in refused connections from the build host (OQ-14), so the request and
response shapes follow the documented public page and are read through
`core.normalize.gem.ALIASES` (OQ-61); the first live smoke from an Indian/cloud IP confirms
them (data changes only).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import structlog

from app.adapters.base import (
    AdapterHealth,
    AdapterStatus,
    DegradedNotes,
    DocumentRef,
    OpportunityIn,
    RawRecord,
    safe_int,
)
from app.adapters.http import PoliteClient
from app.adapters.registry import register
from app.core.config import Settings, get_settings
from app.core.normalize.gem import (
    SOURCE_ID,
    bid_number,
    document_url,
    field,
    normalize_gem,
    parse_gem_datetime,
)

log = structlog.get_logger(__name__)

PAGE_SIZE = 10  # what the public page requests; the endpoint decides the actual size
DOCS_PATH = ("response", "response", "docs")
TOTAL_PATH = ("response", "response", "numFound")
START_PATH = ("response", "response", "start")


class GemApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"GeM listing returned HTTP {status}: {detail}")
        self.status = status


def listing_payload(page: int, *, search: str = "") -> dict[str, Any]:
    """The form body the public All Bids page posts (ongoing bids, newest end date first)."""
    return {
        "page": page,
        "param": {"searchBid": search, "searchType": "fullText"},
        "filter": {
            "bidStatusType": "ongoing_bids",
            "byType": "all",
            "highBidValue": "",
            "byEndDate": {"from": "", "to": ""},
            "sort": "Bid-End-Date-Latest",
        },
    }


def encode_cursor(page: int) -> str:
    return json.dumps({"page": page})


def decode_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        data = json.loads(cursor)
    except ValueError:
        return None
    page = data.get("page") if isinstance(data, dict) else None
    return int(page) if isinstance(page, int) and page >= 1 else None


def _dig(data: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


@register
class GemAdapter:
    source_id = SOURCE_ID
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "Government e-Marketplace (GeM)"

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        max_pages: int | None = None,
        download_documents: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.max_pages = self.settings.gem_max_pages if max_pages is None else max_pages
        self.download_documents = download_documents
        self.listing_url = self.settings.gem_bids_url
        self._notes = DegradedNotes()
        self._last_error: str | None = None
        self._last_run_at: datetime | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(settings=self.settings)
        return self._client

    # -- fetch ------------------------------------------------------------------------
    def _page(self, page: int, *, search: str = "") -> dict[str, Any]:
        response = self.client.post(
            self.listing_url,
            data={"payload": json.dumps(listing_payload(page, search=search))},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            source_id=self.source_id,
            external_id=f"all-bids/page-{page}" if not search else f"search/{search}",
        )
        if response.status_code != 200:
            raise GemApiError(response.status_code, response.text[:200])
        try:
            data = response.json()
        except ValueError as exc:
            raise GemApiError(200, "listing body is not JSON") from exc
        if not isinstance(data, dict):
            raise GemApiError(200, "unexpected listing body")
        data["_raw_ref"] = response.raw_ref
        data["_fetched_at"] = response.fetched_at
        return data

    def _docs(self, data: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
        docs = _dig(data, DOCS_PATH)
        if docs is None:
            self._notes.add(
                "listing has no response.response.docs key (layout change?); "
                f"keys={sorted(k for k in data if not str(k).startswith('_'))[:8]}"
            )
            return [], None
        if not isinstance(docs, list):
            self._notes.add("'docs' is not a list (layout change?)")
            return [], None
        total_raw = _dig(data, TOTAL_PATH)
        total: int | None = None
        if total_raw is not None:
            if isinstance(total_raw, int) and not isinstance(total_raw, bool):
                total = total_raw
            else:
                self._notes.add(f"'numFound' is not an integer: {total_raw!r}")
                total = safe_int(total_raw, 0) or None
        valid = [d for d in docs if isinstance(d, dict) and bid_number(d)]
        if len(valid) != len(docs):
            self._notes.add(f"{len(docs) - len(valid)} bid record(s) without a bid number skipped")
        return valid, total

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        self._last_run_at = self._now()
        self._notes.reset()
        page = decode_cursor(cursor) or 1
        seen: set[str] = set()
        fetched = 0
        try:
            while page <= self.max_pages:
                data = self._page(page)
                docs, total = self._docs(data)
                start = _dig(data, START_PATH)
                if isinstance(start, int) and not isinstance(start, bool) and start >= 0:
                    fetched = start + len(docs)  # the endpoint's own offset (cursor resume)
                else:
                    fetched += len(docs)
                new_on_page = 0
                for doc in docs:
                    number = bid_number(doc)
                    assert number is not None
                    if number in seen:
                        continue
                    seen.add(number)
                    new_on_page += 1
                    started = parse_gem_datetime(field(doc, "start_date"))
                    if started is not None and started < since:
                        continue
                    yield RawRecord(
                        source_id=self.source_id,
                        external_id=number,
                        fetched_at=data["_fetched_at"],
                        payload=doc,
                        raw_ref=data["_raw_ref"],
                        meta={"cursor": encode_cursor(page), "page": page, "total": total},
                    )
                # stop at the reported total, an empty page, or a page that only repeats
                # bids already seen (a wrong numFound must not make us loop to max_pages)
                if not new_on_page or (total is not None and fetched >= total):
                    break
                page += 1
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    # -- detail / documents / normalize -------------------------------------------------
    def fetch_detail(self, external_id: str) -> RawRecord:
        """GeM has no public per-bid HTML detail: the listing searched by bid number is the
        detail record (the PDF carries the rest, see fetch_documents)."""
        data = self._page(1, search=external_id)
        docs, _ = self._docs(data)
        match = next((d for d in docs if bid_number(d) == external_id), None)
        if match is None:
            raise LookupError(f"GeM bid {external_id} not found in the public listing")
        return RawRecord(
            source_id=self.source_id,
            external_id=external_id,
            fetched_at=data["_fetched_at"],
            payload=match,
            raw_ref=data["_raw_ref"],
        )

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        """The bid document PDF, downloaded (archived under raw/gem/...) and hashed."""
        payload = raw.payload if isinstance(raw.payload, dict) else {}
        url = document_url(payload, self.settings.gem_bid_page_url)
        if not url:
            return []
        ref = DocumentRef(
            url=url,
            file_name=f"{raw.external_id.replace('/', '-')}.pdf",
            mime_type="application/pdf",
        )
        if not self.download_documents:
            return [ref]
        try:
            response = self.client.get(
                url, source_id=self.source_id, external_id=f"{raw.external_id}/document"
            )
        except Exception as exc:
            log.warning("gem.document_download_failed", bid=raw.external_id, error=str(exc))
            return [ref]
        if response.status_code != 200 or not response.content:
            log.warning(
                "gem.document_unavailable", bid=raw.external_id, status=response.status_code
            )
            return [ref]
        content_type = response.content_type.split(";")[0].strip() or "application/pdf"
        return [
            ref.model_copy(
                update={
                    "sha256": hashlib.sha256(response.content).hexdigest(),
                    "size": len(response.content),
                    "mime_type": content_type,
                }
            )
        ]

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        if not isinstance(raw.payload, dict):
            raise ValueError("GeM records are JSON objects")
        return normalize_gem(
            raw.payload,
            bid_page_url=self.settings.gem_bid_page_url,
            seller_registration_url=self.settings.gem_seller_registration_url,
            external_id=raw.external_id,
        )

    # -- health -------------------------------------------------------------------------
    def health(self) -> AdapterHealth:
        if self._last_error:
            return AdapterHealth(AdapterStatus.FAILING, self._last_run_at, self._last_error)
        if self._notes:
            return AdapterHealth(AdapterStatus.DEGRADED, self._last_run_at, self._notes.message)
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, None)
