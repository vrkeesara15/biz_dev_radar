"""GePNIC state-portal listing rows -> OpportunityIn (SPEC 2 row 9, 5.2; M3-05). Pure.

`PortalConfig` is one entry of app/adapters/gepnic_configs.yaml (the adapter module loads
the file; this module only knows the values). A row comes from either the front page
"Latest Tenders" marquee (title, reference, closing, opening; no tender id or
organisation) or an organisation's tender listing (6-column table with the GePNIC tender
id 2026_ABC_123456_1 and the organisation chain). Per-tender links on GePNIC are Tapestry
`DirectLink`s bound to the visitor's session and expire, so every record keeps the
portal's own search page as the fallback (`extra.portal_search_url`) and is
`detail_status = manual` (SPEC 5.1: "store tender ID + portal search URL").
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from app.core.config import Region
from app.core.dates import IST
from app.core.normalize.nicgep import (
    DEFAULT_DATE_FORMATS,
    TENDER_ID_RE,
    TenderRow,
    parse_portal_datetime,
    split_organisation_chain,
)
from app.core.normalize.reference import normalized_reference
from app.core.opportunity import DetailStatus, NoticeType, OpportunityIn

PAGE_HOME = "home_latest"
PAGE_ORG = "organisation_listing"
_SLUG = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class PortalConfig:
    source_id: str
    display_name: str
    state: str
    portal_home: str
    base_url: str
    home_page: str = ""
    org_list_page: str = "?page=FrontEndTendersByOrganisation&service=page"
    latest_page: str = "?page=FrontEndLatestActiveTenders&service=page"  # CAPTCHA: never fetched
    search_page: str = "?page=FrontEndAdvancedSearch&service=page"
    date_formats: tuple[str, ...] = DEFAULT_DATE_FORMATS
    tz: str = IST
    schedule: str = "0 */3 * * *"
    enabled: bool = True
    health_status: str | None = None  # for disabled entries: robots_disallowed | not_implemented
    reason: str | None = None
    robots_checked: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortalConfig:
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        values = {k: v for k, v in data.items() if k in known}
        rest = {k: v for k, v in data.items() if k not in known}
        formats = values.get("date_formats")
        if isinstance(formats, list | tuple) and formats:
            values["date_formats"] = tuple(str(f) for f in formats)
        else:
            values.pop("date_formats", None)
        for name in ("source_id", "display_name", "state", "portal_home", "base_url"):
            if not values.get(name):
                raise ValueError(f"GePNIC portal config needs {name!r}: {data!r}")
        if values.get("health_status") is not None:
            values["health_status"] = str(values["health_status"])
        if values.get("robots_checked") is not None:
            values["robots_checked"] = str(values["robots_checked"])
        return cls(**values, extra=rest)

    def page_url(self, page: str) -> str:
        """base_url + a page query ('' = the front page)."""
        return self.base_url + page if page else self.base_url

    @property
    def home_url(self) -> str:
        return self.page_url(self.home_page)

    @property
    def org_list_url(self) -> str:
        return self.page_url(self.org_list_page)

    @property
    def search_url(self) -> str:
        return self.page_url(self.search_page)

    def absolute(self, href: str | None) -> str | None:
        if not href:
            return None
        return urljoin(self.base_url, href)


def row_to_payload(row: TenderRow, *, page: str, organisation: str | None = None) -> dict[str, Any]:
    return {
        "title": row.title,
        "reference": row.reference,
        "tender_id": row.tender_id,
        "organisation": row.organisation or organisation,
        "published": row.published,
        "closing": row.closing,
        "opening": row.opening,
        "detail_url": row.detail_url,
        "corrigendum": row.corrigendum,
        "page": page,
        "cells": list(row.raw_cells),
    }


def dedupe_key(payload: dict[str, Any]) -> str:
    """Same tender listed on the front page and in an organisation listing.

    The front-page marquee has no tender id, so the key both shapes share is the
    reference number plus the title; the tender id is only the fallback for a row that
    somehow carries neither.
    """
    ref = normalized_reference(payload.get("reference")) or ""
    title = " ".join(str(payload.get("title") or "").lower().split())
    if ref or title:
        return f"rt:{ref}|{title}"
    tender_id = payload.get("tender_id")
    if tender_id and TENDER_ID_RE.match(str(tender_id)):
        return f"id:{tender_id}"
    return "rt:|"


def external_id_for(payload: dict[str, Any]) -> str:
    """The GePNIC tender id when the row has one, else reference + title hash (front page)."""
    tender_id = payload.get("tender_id")
    if tender_id and TENDER_ID_RE.match(str(tender_id)):
        return str(tender_id)
    ref = _SLUG.sub("-", str(payload.get("reference") or "")).strip("-")
    title = " ".join(str(payload.get("title") or "").lower().split())
    digest = hashlib.sha1(f"{ref}|{title}".encode()).hexdigest()[:12]
    return f"{ref}-{digest}" if ref else f"t-{digest}"


def normalize_gepnic(
    payload: dict[str, Any], portal: PortalConfig, *, external_id: str | None = None
) -> OpportunityIn:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError(f"{portal.source_id} row has no title")

    def dt(text: Any) -> datetime | None:
        parsed = parse_portal_datetime(
            text if text is None else str(text), formats=portal.date_formats, tz=portal.tz
        )
        return parsed.utc if parsed else None

    chain = split_organisation_chain(payload.get("organisation"))
    link = portal.absolute(payload.get("detail_url"))
    tender_id = payload.get("tender_id")
    return OpportunityIn(
        source_id=portal.source_id,
        external_id=external_id or external_id_for(payload),
        # the per-tender link when the portal gave one (session-bound, expires), else the
        # portal search page a human can use with the tender id / reference
        source_url=link or portal.search_url,
        region=Region.IN,
        country="IN",
        currency="INR",
        notice_type=NoticeType.CORRIGENDUM if payload.get("corrigendum") else NoticeType.RFP,
        title=title,
        solicitation_number=payload.get("reference") or None,
        buyer_org=chain[0] if chain else None,
        buyer_sub_org=chain[1] if len(chain) > 1 else None,
        buyer_office=chain[-1] if len(chain) > 2 else None,
        buyer_hierarchy=chain,
        posted_at=dt(payload.get("published")),
        response_due_at=dt(payload.get("closing")),
        opening_at=dt(payload.get("opening")),
        source_tz=portal.tz,
        detail_status=DetailStatus.MANUAL,
        extra={
            "portal": portal.source_id,
            "portal_name": portal.display_name,
            "state": portal.state,
            "tender_id": str(tender_id) if tender_id else None,
            "listing_page": payload.get("page"),
            "organisation_raw": payload.get("organisation"),
            "corrigendum": bool(payload.get("corrigendum")),
            "portal_search_url": portal.search_url,
            "link_is_session_bound": bool(link and "DirectLink" in link),
        },
    )


def config_ids(configs: Sequence[PortalConfig]) -> list[str]:
    return [c.source_id for c in configs]
