"""GeM (bidplus.gem.gov.in) public bid listing record -> OpportunityIn (SPEC 2 row 8, 5.2).

The public "All Bids" page is JavaScript-driven and backed by a Solr-style JSON endpoint;
documents come back as objects whose fields are (mostly) single-item lists. This module
reads them through `ALIASES` (several accepted spellings per field, PROGRESS.m3.md OQ-61)
so a renamed key is a data change, not a code change:

    doc = {"b_id": [7891234], "b_bid_number": ["GEM/2026/B/1234567"], "b_category_name":
           ["Desktop Computers"], "b_total_quantity": [120], "ba_official_details_minName":
           ["Ministry of Railways"], "ba_official_details_deptName": ["South Central Railway"],
           "final_start_date_sort": "2026-06-27T14:53:00Z", "final_end_date_sort":
           "2026-07-17T14:53:00Z", "b_bid_type": ["Bid"]}

Bid type "RA" (reverse auction) -> notice_type reverse_auction, everything else gem_bid.
The bid document PDF (`showbidDocument/<id>`) is the record's document and the bid page;
the seller-registration link is surfaced in `extra` (SPEC 2 row 8: "GeM seller
registration to bid").
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import Region
from app.core.dates import IST, parse_in
from app.core.opportunity import DetailStatus, DocumentKind, DocumentRef, NoticeType, OpportunityIn

SOURCE_ID = "gem"
PORTAL_HOME = "https://gem.gov.in/"
BID_NUMBER_RE = re.compile(r"^GEM/\d{4}/[A-Z]{1,3}/\d+$", re.IGNORECASE)

ALIASES: dict[str, tuple[str, ...]] = {
    "bid_id": ("b_id", "bid_id", "id"),
    "bid_number": ("b_bid_number", "bid_number", "bidNumber", "bid_no"),
    "category": ("b_category_name", "category_name", "category", "item_category"),
    "quantity": ("b_total_quantity", "total_quantity", "quantity"),
    "ministry": ("ba_official_details_minName", "ministry", "ministry_name", "min_name"),
    "department": ("ba_official_details_deptName", "department", "department_name", "dept_name"),
    "organisation": ("ba_official_details_orgName", "organisation", "organization", "org_name"),
    "office": ("ba_official_details_officeName", "office", "office_name"),
    "start_date": ("final_start_date_sort", "b_bid_start_date", "bid_start_date", "start_date"),
    "end_date": ("final_end_date_sort", "b_bid_end_date", "bid_end_date", "end_date"),
    "end_date_display": ("bid_end_date_display", "b_bid_end_date_display", "end_date_display"),
    "bid_type": ("b_bid_type", "bid_type", "type"),
    "is_ra": ("b_is_ra", "is_ra", "ra"),
    "document_url": ("b_bid_document", "bid_document", "document_url", "pdf_url"),
    "estimated_value": ("b_estimated_value", "estimated_value", "bid_value"),
    "emd": ("b_emd_amount", "emd_amount", "emd"),
    "state": ("ba_official_details_stateName", "state", "state_name"),
}


def first(value: Any) -> Any:
    """Solr multi-valued fields arrive as one-item lists; unwrap them."""
    if isinstance(value, list | tuple):
        return value[0] if value else None
    return value


def field(doc: Mapping[str, Any], name: str) -> Any:
    for key in ALIASES[name]:
        if key in doc:
            value = first(doc[key])
            if value not in (None, ""):
                return value
    return None


def bid_number(doc: Mapping[str, Any]) -> str | None:
    value = field(doc, "bid_number")
    text = str(value).strip() if value is not None else ""
    return text or None


def is_reverse_auction(doc: Mapping[str, Any]) -> bool:
    flag = field(doc, "is_ra")
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str) and flag.strip().lower() in {"1", "true", "yes", "y"}:
        return True
    if isinstance(flag, int) and flag == 1:
        return True
    kind = str(field(doc, "bid_type") or "").strip().lower()
    return kind in {"ra", "reverse auction", "reverse_auction"} or kind.startswith("ra ")


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def parse_gem_datetime(value: Any) -> datetime | None:
    """ISO (Solr, UTC 'Z'), '17-Jul-2026 08:23 PM', '17-07-2026 20:23:00' (IST) or epoch."""
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        seconds = float(value) / (1000 if value > 1e11 else 1)
        return datetime.fromtimestamp(seconds, tz=UTC)
    parsed = parse_in(str(value), IST)
    return parsed.utc if parsed else None


def document_url(doc: Mapping[str, Any], page_template: str) -> str | None:
    explicit = field(doc, "document_url")
    if isinstance(explicit, str) and explicit.startswith("http"):
        return explicit
    bid_id = field(doc, "bid_id")
    if bid_id is None:
        return None
    return page_template.format(bid_id=bid_id)


def normalize_gem(
    doc: Mapping[str, Any],
    *,
    bid_page_url: str,
    seller_registration_url: str,
    external_id: str | None = None,
) -> OpportunityIn:
    number = bid_number(doc)
    if not number:
        raise ValueError("GeM record has no bid number")
    category = field(doc, "category")
    ministry = field(doc, "ministry")
    department = field(doc, "department")
    organisation = field(doc, "organisation")
    office = field(doc, "office")
    chain = [str(x).strip() for x in (ministry, department, organisation, office) if x]
    quantity = _int(field(doc, "quantity"))
    page_url = document_url(doc, bid_page_url)
    title = f"{category or 'GeM bid'} ({number})"
    if quantity:
        title = f"{category or 'GeM bid'} x {quantity} ({number})"
    documents: list[DocumentRef] = []
    if page_url:
        documents.append(
            DocumentRef(
                url=page_url,
                file_name=f"{number.replace('/', '-')}.pdf",
                kind=DocumentKind.ATTACHMENT,
                mime_type="application/pdf",
            )
        )
    end_display = field(doc, "end_date_display")
    return OpportunityIn(
        source_id=SOURCE_ID,
        external_id=external_id or number,
        source_url=page_url or PORTAL_HOME,
        region=Region.IN,
        country="IN",
        currency="INR",
        notice_type=NoticeType.REVERSE_AUCTION if is_reverse_auction(doc) else NoticeType.GEM_BID,
        title=title,
        solicitation_number=number,
        buyer_org=chain[0] if chain else None,
        buyer_sub_org=chain[1] if len(chain) > 1 else None,
        buyer_office=chain[-1] if len(chain) > 2 else None,
        buyer_hierarchy=chain,
        india_category=[str(category)] if category else [],
        posted_at=parse_gem_datetime(field(doc, "start_date")),
        response_due_at=parse_gem_datetime(field(doc, "end_date"))
        or parse_gem_datetime(end_display),
        source_tz=IST,
        eligibility={"requires_gem_registration": True},
        documents=documents,
        detail_status=DetailStatus.PENDING,
        extra={
            "gem_bid_id": str(field(doc, "bid_id")) if field(doc, "bid_id") is not None else None,
            "bid_type": "RA" if is_reverse_auction(doc) else "Bid",
            "item_category": category,
            "quantity": quantity,
            "ministry": ministry,
            "department": department,
            "state": field(doc, "state"),
            "bid_end_display": end_display,
            "gem_seller_registration_url": seller_registration_url,
        },
    )
