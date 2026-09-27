"""Documented adapter stubs (SPEC 2 rows 5-6, 10; OQ-4, OQ-5; M2-17).

Each stub is a registered SourceAdapter with `enabled = False`: it gets a `sources` row and
shows up in the admin health listing as `not_implemented`, the beat schedule and the
nightly smoke skip it, the contract tests skip it explicitly, and `run_source` refuses
to run it. Turning one into a real adapter = implement fetch/normalize, record fixtures,
add a ContractSpec and flip `enabled` (see docs/adapters.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import ClassVar

from app.adapters.base import (
    AdapterHealth,
    AdapterStatus,
    DocumentRef,
    OpportunityIn,
    RawRecord,
    RegionCode,
)
from app.adapters.registry import register

DOCS = "docs/adapters.md"


class StubAdapter:
    """A source we know about but do not crawl yet. Never yields, never raises from fetch."""

    source_id: ClassVar[str] = ""
    region: ClassVar[RegionCode] = "us"
    schedule: ClassVar[str] = "0 6 * * *"
    enabled: ClassVar[bool] = False
    display_name: ClassVar[str] = ""
    portal_url: ClassVar[str] = ""
    # why it is a stub and what implementing it needs (shown in health.message)
    reason: ClassVar[str] = "not implemented yet"

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]:
        return iter(())

    def fetch_detail(self, external_id: str) -> RawRecord:
        raise NotImplementedError(f"{self.source_id}: {self.reason} (see {DOCS})")

    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]:
        return []

    def normalize(self, raw: RawRecord) -> OpportunityIn:
        raise NotImplementedError(f"{self.source_id}: {self.reason} (see {DOCS})")

    def health(self) -> AdapterHealth:
        return AdapterHealth(
            AdapterStatus.NOT_IMPLEMENTED, None, f"{self.display_name}: {self.reason}"
        )


@register
class DefenseGovAwardsAdapter(StubAdapter):
    """defense.gov daily contract announcements (SPEC 2 row 5).

    Public HTML pages (https://www.defense.gov/News/Contracts/), one article per day listing
    every DoD award >= USD 7.5M with vendor, amount, place of performance, completion date
    and the awarding activity. Value: incumbent / recompete signals a day earlier than
    the SAM.gov awards search. Implementing: PoliteClient GET of the listing + article
    pages, per-paragraph parsing into `award` notices (status awarded) feeding
    awards_enrichment like sam_awards; robots.txt allows crawling, keep 1 req/s.
    """

    source_id = "defense_gov_awards"
    region = "us"
    schedule = "0 5 * * *"
    display_name = "Defense.gov Contract Announcements"
    portal_url = "https://www.defense.gov/News/Contracts/"
    reason = "HTML article parser not written; awards come from sam_awards/usaspending for now"


@register
class SledGenericAdapter(StubAdapter):
    """US state and local (SLED) portals (SPEC 2 row 6, OQ-5).

    50 states x many platforms (Bonfire, BidNet, Periscope/ Bidsync, OpenGov, DemandStar,
    state-run portals); most need vendor registration for documents but list titles,
    agencies and due dates publicly. Plan: a config-driven generic adapter per platform
    family (like GePNIC for India) plus licensed feeds through PaidFeedAdapter (BidNet,
    GovSpend, HigherGov) where a tenant supplies a key. Compliance: public listing pages
    only, honour robots.txt, no vendor login.
    """

    source_id = "sled_generic"
    region = "us"
    schedule = "0 */6 * * *"
    display_name = "US state and local portals"
    portal_url = "https://www.bidnetdirect.com/"
    reason = "no portal-family parser yet; SLED coverage via paid feeds or a per-platform adapter"


@register
class IrepsAdapter(StubAdapter):
    """IREPS, Indian Railways e-procurement (SPEC 2 row 10, phase 2).

    https://www.ireps.gov.in/ lists tenders (works, goods, services, sales) per zone with
    public tender search pages; documents and bidding need vendor registration + DSC.
    Implementing: listing search (POST form, paginated), tender detail page (public
    fields: tender no, title, due date/time, EMD, bidding start), `detail_status =
    manual` where a CAPTCHA appears (SPEC 5.1). Rate 1 req/s (India default), robots
    check on first live run.
    """

    source_id = "ireps"
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "IREPS (Indian Railways)"
    portal_url = "https://www.ireps.gov.in/"
    reason = "phase 2 (OQ-4): listing parser and CAPTCHA-free detail path not verified yet"


@register
class DefprocAdapter(StubAdapter):
    """defproc.gov.in, Ministry of Defence procurement portal (SPEC 2 row 10, phase 2).

    GePNIC-based; the generic GePNIC adapter (M3) should cover it with a portal config
    (base URL, organisation list pages, date formats) once the M3 adapter exists. Kept as
    a separate source id so tenants can enable/disable it and so attribution reads
    "Defence Procurement Portal".
    """

    source_id = "defproc"
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "Defence Procurement Portal (defproc.gov.in)"
    portal_url = "https://defproc.gov.in/"
    reason = "phase 2 (OQ-4): to be a GePNIC portal config once M3 ships the generic adapter"
