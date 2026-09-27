"""Generic GePNIC state-portal adapter (SPEC 2 row 9, 5.2; M3-05).

One class, configured per portal from `gepnic_configs.yaml` (next to this module). At
import time every entry becomes a registered adapter class (`GePNICAdapter` subclass with
the entry's source_id / schedule / enabled flag), so the registry, the `sources` rows,
Celery beat, the admin listing and the contract tests see `gepnic_tn`, `gepnic_up`,
`gepnic_central` (enabled) and `gepnic_mh` / `gepnic_ts` (disabled, health
robots_disallowed / not_implemented) without any per-state code.

Per run (every 3 h) an enabled portal reads two captcha-free pages, both through
PoliteClient (1 req/s for *.gov.in, robots.txt, raw archive):

1. the front page, whose "Latest Tenders" marquee lists title / reference / closing /
   opening for the newest tenders;
2. "Tenders by Organisation": the organisation index, then the first
   GEPNIC_MAX_ORGS_PER_RUN organisation listings (6-column tender table with tender ids
   and the organisation chain). The links are Tapestry DirectLinks bound to the visitor
   session (cookies live in the client for the run).

"Latest Active Tenders" (`latest_page`) needs a CAPTCHA on GePNIC and is never fetched
(OQ-62). Per-tender links expire, so every record is `detail_status = manual` and carries
the portal search page as `extra.portal_search_url` (SPEC 5.1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import structlog
import yaml

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
from app.core.normalize.gepnic import (
    PAGE_HOME,
    PAGE_ORG,
    PortalConfig,
    dedupe_key,
    external_id_for,
    normalize_gepnic,
    row_to_payload,
)
from app.core.normalize.nicgep import (
    TenderRow,
    parse_home_latest,
    parse_listing_table,
    parse_org_index,
)

log = structlog.get_logger(__name__)

CONFIG_PATH = Path(__file__).with_name("gepnic_configs.yaml")
DISABLED_STATUSES = {
    "robots_disallowed": AdapterStatus.ROBOTS_DISALLOWED,
    "not_implemented": AdapterStatus.NOT_IMPLEMENTED,
}


class GePNICPortalError(RuntimeError):
    def __init__(self, source_id: str, url: str, status: int) -> None:
        super().__init__(f"{source_id}: portal returned HTTP {status} for {url}")
        self.url = url
        self.status = status


def load_portal_configs(path: Path | None = None) -> list[PortalConfig]:
    """Every portal entry of the YAML file, in file order (validated)."""
    data = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    entries = data.get("portals") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path or CONFIG_PATH}: expected a top-level 'portals' list")
    configs = [PortalConfig.from_dict(dict(entry)) for entry in entries]
    ids = [c.source_id for c in configs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate GePNIC source ids in config: {ids}")
    return configs


class GePNICAdapter:
    """Configured by the class attribute `portal` (set per subclass by `adapter_class`)."""

    # class attributes on the per-portal subclasses (registry contract); an ad-hoc instance
    # built with `portal=` overrides them on the instance
    portal: PortalConfig
    source_id: str = ""
    region: ClassVar[str] = "in"
    schedule: str = "0 */3 * * *"
    enabled: bool = True
    display_name: str = ""
    portal_url: str = ""

    def __init__(
        self,
        *,
        client: PoliteClient | None = None,
        settings: Settings | None = None,
        now: Callable[[], datetime] | None = None,
        max_orgs: int | None = None,
        portal: PortalConfig | None = None,
    ) -> None:
        if portal is not None:  # ad-hoc instance (tests, one-off runs) outside the registry
            self.portal = portal
            self.source_id = portal.source_id
            self.schedule = portal.schedule
            self.enabled = portal.enabled
            self.display_name = portal.display_name
            self.portal_url = portal.portal_home
        self.settings = settings or get_settings()
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))
        self.max_orgs = (
            self.settings.gepnic_max_orgs_per_run if max_orgs is None else max(max_orgs, 0)
        )
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
        if not self.enabled:
            log.info("gepnic.disabled", source=self.source_id, reason=self.portal.reason)
            return
        seen: set[str] = set()
        try:
            # organisation listings first: they carry tender ids and the organisation chain
            yield from self._organisations(since, seen)
            yield from self._home(since, seen)
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_error = None

    def _records(
        self,
        rows: list[TenderRow],
        *,
        page: str,
        seen: set[str],
        raw_ref: str | None,
        fetched_at: datetime,
        organisation: str | None = None,
    ) -> Iterator[RawRecord]:
        for row in rows:
            payload = row_to_payload(row, page=page, organisation=organisation)
            key = dedupe_key(payload)
            if key in seen:
                continue
            seen.add(key)
            yield RawRecord(
                source_id=self.source_id,
                external_id=external_id_for(payload),
                fetched_at=fetched_at,
                payload=payload,
                content_type="text/html",
                raw_ref=raw_ref,
                meta={"page": page, "portal": self.portal.source_id},
            )

    def _home(self, since: datetime, seen: set[str]) -> Iterator[RawRecord]:
        url = self.portal.home_url
        html, raw_ref, fetched_at, status = self._get(url, "home")
        if status != 200:
            raise GePNICPortalError(self.source_id, url, status)
        outcome = parse_home_latest(html)
        for note in outcome.notes:
            self._notes.add(f"home page: {note}")
        yield from self._records(
            outcome.rows, page=PAGE_HOME, seen=seen, raw_ref=raw_ref, fetched_at=fetched_at
        )

    def _organisations(self, since: datetime, seen: set[str]) -> Iterator[RawRecord]:
        if not self.portal.org_list_page or self.max_orgs == 0:
            return
        url = self.portal.org_list_url
        html, _, _, status = self._get(url, "tenders-by-organisation")
        if status != 200:
            self._notes.add(f"tenders by organisation page unavailable (HTTP {status})")
            return
        index = parse_org_index(html)
        if not index.found:
            self._notes.add("tenders by organisation: no organisation index (layout change?)")
            return
        followed = 0
        for org in index.rows:
            if followed >= self.max_orgs:
                break
            if not org.url or not org.count:
                continue
            followed += 1
            org_url = self.portal.absolute(org.url)
            assert org_url is not None
            org_html, org_ref, org_fetched, org_status = self._get(
                org_url, f"tenders-by-organisation/{followed}"
            )
            if org_status != 200:
                self._notes.add(f"organisation listing {org.name!r}: HTTP {org_status}")
                continue
            listing = parse_listing_table(org_html)
            for note in listing.notes:
                self._notes.add(f"organisation listing {org.name!r}: {note}")
            yield from self._records(
                listing.rows,
                page=PAGE_ORG,
                seen=seen,
                raw_ref=org_ref,
                fetched_at=org_fetched,
                organisation=org.name,
            )

    # -- detail / documents / normalize -------------------------------------------------
    def fetch_detail(self, external_id: str) -> RawRecord:
        """Per-tender pages are session-bound DirectLinks that expire: no request; the
        record points a human at the portal search page with the tender id."""
        return RawRecord(
            source_id=self.source_id,
            external_id=external_id,
            fetched_at=self._now(),
            payload={"detail_status": "manual", "portal_search_url": self.portal.search_url},
            meta={"session_bound_links": True},
        )

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        """Tender documents hang off the (expiring) detail page; none from the listings."""
        return []

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        if not isinstance(raw.payload, dict):
            raise ValueError("GePNIC records are parsed listing rows")
        payload: dict[str, Any] = raw.payload
        return normalize_gepnic(payload, self.portal, external_id=raw.external_id)

    # -- health -------------------------------------------------------------------------
    def health(self) -> AdapterHealth:
        if not self.enabled:
            status = DISABLED_STATUSES.get(
                self.portal.health_status or "", AdapterStatus.NOT_IMPLEMENTED
            )
            return AdapterHealth(
                status,
                self._last_run_at,
                f"{self.display_name}: {self.portal.reason or 'disabled'}",
            )
        if self._last_error:
            return AdapterHealth(AdapterStatus.FAILING, self._last_run_at, self._last_error)
        if self._notes:
            return AdapterHealth(AdapterStatus.DEGRADED, self._last_run_at, self._notes.message)
        return AdapterHealth(AdapterStatus.OK, self._last_run_at, None)


def adapter_class(config: PortalConfig) -> type[GePNICAdapter]:
    """A registrable subclass for one portal (class attributes = the registry contract)."""
    name = "GePNIC" + "".join(part.title() for part in config.source_id.split("_"))
    doc = f"{config.display_name} ({config.base_url}); configured in {CONFIG_PATH.name}."
    return type(
        name,
        (GePNICAdapter,),
        {
            "portal": config,
            "source_id": config.source_id,
            "region": "in",
            "schedule": config.schedule,
            "enabled": config.enabled,
            "display_name": config.display_name,
            "portal_url": config.portal_home,
            "__doc__": doc,
        },
    )


PORTAL_CONFIGS: list[PortalConfig] = load_portal_configs()
PORTAL_ADAPTERS: dict[str, type[GePNICAdapter]] = {
    config.source_id: register(adapter_class(config)) for config in PORTAL_CONFIGS
}
