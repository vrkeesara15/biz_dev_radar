"""CPPP (eprocure.gov.in) listing row -> OpportunityIn (SPEC 2 row 7, 5.2). Pure.

CPPP is the central publication point; its captcha-free listing pages give the tender id,
reference number, title, organisation, e-published / closing / opening dates and a
corrigendum flag. The per-tender "tendersfullview" link is the official page a human
opens; the portal's search and archive need a CAPTCHA, so every record is stored with
`detail_status = manual` and the portal search URL (SPEC 5.1).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from app.core.config import Region
from app.core.dates import IST
from app.core.normalize.nicgep import (
    TenderRow,
    decode_cppp_token,
    parse_portal_datetime,
    split_organisation_chain,
)
from app.core.opportunity import DetailStatus, NoticeType, OpportunityIn

SOURCE_ID = "cppp"
PORTAL_HOME = "https://eprocure.gov.in/cppp/"
LATEST_ACTIVE_URL = "https://eprocure.gov.in/cppp/latestactivetendersnew"
SEARCH_URL = "https://eprocure.gov.in/cppp/tendersearch"  # CAPTCHA-gated: humans only
PAGE_LATEST = "latest_active"
PAGE_BY_ORG = "by_organisation"

_SLUG = re.compile(r"[^A-Za-z0-9]+")


def row_to_payload(row: TenderRow, *, page: str) -> dict[str, Any]:
    """JSON-safe RawRecord payload for a listing row."""
    return {
        "title": row.title,
        "reference": row.reference,
        "tender_id": row.tender_id,
        "organisation": row.organisation,
        "published": row.published,
        "closing": row.closing,
        "opening": row.opening,
        "detail_url": row.detail_url,
        "corrigendum": row.corrigendum,
        "page": page,
        "cells": list(row.raw_cells),
    }


def external_id_for(payload: dict[str, Any]) -> str:
    """Stable id: the internal tender id inside the detail link, else reference + tender
    code, else a hash of title/organisation/closing."""
    token = decode_cppp_token(payload.get("detail_url"))
    if token.get("internal_id"):
        return token["internal_id"]
    reference, code = payload.get("reference"), payload.get("tender_id")
    if reference and code:
        return _SLUG.sub("-", f"{reference}-{code}").strip("-")
    basis = "|".join(
        str(payload.get(k) or "") for k in ("title", "organisation", "closing", "reference")
    )
    return "h-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def _dt(text: str | None) -> datetime | None:
    parsed = parse_portal_datetime(text)
    return parsed.utc if parsed else None


def normalize_cppp(payload: dict[str, Any], *, external_id: str | None = None) -> OpportunityIn:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("CPPP row has no title")
    token = decode_cppp_token(payload.get("detail_url"))
    chain = split_organisation_chain(payload.get("organisation"))
    reference = payload.get("reference") or token.get("reference")
    extra: dict[str, Any] = {
        "tender_id": payload.get("tender_id") or token.get("code"),
        "cppp_internal_id": token.get("internal_id"),
        "listing_page": payload.get("page"),
        "organisation_raw": payload.get("organisation"),
        "corrigendum": bool(payload.get("corrigendum")),
        # detail/search pages need a CAPTCHA: a human opens this (SPEC 5.1)
        "manual_detail_url": SEARCH_URL,
    }
    if token.get("expires"):
        extra["detail_link_expires_at"] = datetime.fromtimestamp(
            int(token["expires"]), tz=UTC
        ).isoformat()
    return OpportunityIn(
        source_id=SOURCE_ID,
        external_id=external_id or external_id_for(payload),
        source_url=payload.get("detail_url") or LATEST_ACTIVE_URL,
        region=Region.IN,
        country="IN",
        currency="INR",
        notice_type=NoticeType.CORRIGENDUM if payload.get("corrigendum") else NoticeType.RFP,
        title=title,
        solicitation_number=reference,
        buyer_org=chain[0] if chain else None,
        buyer_sub_org=chain[1] if len(chain) > 1 else None,
        buyer_office=chain[-1] if len(chain) > 2 else None,
        buyer_hierarchy=chain,
        posted_at=_dt(payload.get("published")),
        response_due_at=_dt(payload.get("closing")),
        opening_at=_dt(payload.get("opening")),
        source_tz=IST,
        detail_status=DetailStatus.MANUAL,
        extra=extra,
    )
