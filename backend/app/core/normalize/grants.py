"""Pure mapping from Grants.gov (search2 hits + fetchOpportunity details) to OpportunityIn.

notice_type: forecast when the record is a forecast (docType 'forecast' / oppStatus
'forecasted'), otherwise grant. Two records can exist for one opportunity (its forecast
and its posted synopsis, or two revisions): `keep_latest()` keeps one per opportunity
number, preferring posted over forecasted, then the later open date, then the higher id.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import Region
from app.core.normalize.common import html_to_text
from app.core.opportunity import (
    Contact,
    DetailStatus,
    DocumentKind,
    DocumentRef,
    NoticeType,
    OpportunityIn,
    OpportunityStatus,
)

SOURCE_ID = "grants_gov"
GRANTS_SOURCE_TZ = "America/New_York"
DETAIL_URL = "https://www.grants.gov/search-results-detail/{id}"
ATTACHMENT_URL = "https://apply07.grants.gov/grantsws/rest/opportunity/att/download/{id}"
DATE_RANGE_OPTIONS = (3, 7, 14, 21, 28, 35, 42, 49, 56)  # search2 "Posted Date - Last N days"

_STATUS_RANK = {"posted": 3, "forecasted": 2, "closed": 1, "archived": 0}
_TZ_ABBREVIATIONS = {"EDT", "EST", "ET"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = unescape(str(value)).strip()
    return text or None


def parse_grants_datetime(value: str | None, *, tz: str = GRANTS_SOURCE_TZ) -> datetime | None:
    """Grants.gov dates: '04/30/2027', 'Apr 30, 2027 12:00:00 AM EDT', '2027-04-30-00-00-00'
    (the *Str variants) or ISO 'YYYY-MM-DD'. All Eastern time -> UTC."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    parts = text.split()
    if parts and parts[-1].upper() in _TZ_ABBREVIATIONS:
        text = " ".join(parts[:-1])
    for fmt in (
        "%m/%d/%Y",
        "%b %d, %Y %I:%M:%S %p",
        "%b %d, %Y",
        "%Y-%m-%d-%H-%M-%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)
    return None


def parse_money(value: Any) -> Decimal | None:
    """'none', '', None -> None; '2,500,000' / '2500000.00' / 2500000 -> Decimal."""
    if value is None:
        return None
    text = str(value).strip().lower().replace(",", "").replace("$", "")
    if not text or text in {"none", "null", "n/a", "tbd"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def date_range_for(days: float) -> str | None:
    """Smallest search2 dateRange option covering `days`; None when older than the widest
    option (the caller then lists everything posted/forecasted)."""
    for option in DATE_RANGE_OPTIONS:
        if days <= option:
            return str(option)
    return None


def is_forecast(hit: Mapping[str, Any] | None, detail: Mapping[str, Any] | None) -> bool:
    if detail is not None and (_clean(detail.get("docType")) == "forecast" or "forecast" in detail):
        return "synopsis" not in detail or _clean(detail.get("docType")) == "forecast"
    if hit is not None:
        return (
            _clean(hit.get("docType")) == "forecast" or _clean(hit.get("oppStatus")) == "forecasted"
        )
    return False


def keep_latest(hits: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One hit per opportunity number (fallback: id), preferring posted > forecasted >
    closed > archived, then the later openDate, then the higher id. Order of first
    appearance is preserved."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for hit in hits:
        key = _clean(hit.get("number")) or f"id:{hit.get('id')}"
        if key not in best:
            best[key] = dict(hit)
            order.append(key)
        elif _rank(hit) > _rank(best[key]):
            best[key] = dict(hit)
    return [best[key] for key in order]


def _rank(hit: Mapping[str, Any]) -> tuple[int, datetime, int]:
    opened = parse_grants_datetime(_clean(hit.get("openDate"))) or datetime.min.replace(tzinfo=UTC)
    try:
        ident = int(str(hit.get("id") or 0))
    except ValueError:
        ident = 0
    return (_STATUS_RANK.get((_clean(hit.get("oppStatus")) or "").lower(), -1), opened, ident)


def _mapping(value: Any) -> Mapping[str, Any]:
    """`value` when it is a mapping, else an empty one (typed for mypy)."""
    if isinstance(value, Mapping):
        block: Mapping[str, Any] = value
        return block
    return {}


def _body(detail: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The synopsis or forecast block of a fetchOpportunity response."""
    if not detail:
        return {}
    synopsis = _mapping(detail.get("synopsis"))
    forecast = _mapping(detail.get("forecast"))
    if synopsis and _clean(detail.get("docType")) != "forecast":
        return synopsis
    return forecast or synopsis


def _first_date(block: Mapping[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        parsed = parse_grants_datetime(_clean(block.get(key)))
        if parsed is not None:
            return parsed
    return None


def map_documents(detail: Mapping[str, Any] | None) -> list[DocumentRef]:
    refs: list[DocumentRef] = []
    if not detail:
        return refs
    for folder in detail.get("synopsisAttachmentFolders") or []:
        for att in folder.get("synopsisAttachments") or []:
            att_id = att.get("id")
            if att_id is None:
                continue
            refs.append(
                DocumentRef(
                    url=ATTACHMENT_URL.format(id=att_id),
                    file_name=_clean(att.get("fileName")),
                    kind=DocumentKind.ATTACHMENT,
                    mime_type=_clean(att.get("mimeType")),
                    size=att.get("fileLobSize")
                    if isinstance(att.get("fileLobSize"), int)
                    else None,
                )
            )
    return refs


def map_eligibility(block: Mapping[str, Any]) -> dict[str, Any]:
    types = [t for t in block.get("applicantTypes") or [] if isinstance(t, Mapping)]
    instruments = [i for i in block.get("fundingInstruments") or [] if isinstance(i, Mapping)]
    categories = [c for c in block.get("fundingActivityCategories") or [] if isinstance(c, Mapping)]
    eligibility: dict[str, Any] = {
        "applicant_types": [d for d in (_clean(t.get("description")) for t in types) if d],
        "applicant_type_codes": [c for c in (_clean(t.get("id")) for t in types) if c],
        "funding_instruments": [
            d for d in (_clean(i.get("description")) for i in instruments) if d
        ],
        "funding_categories": [d for d in (_clean(c.get("description")) for c in categories) if d],
    }
    desc = html_to_text(_clean(block.get("applicantEligibilityDesc")))
    if desc:
        eligibility["eligibility_text"] = desc
    if block.get("costSharing") is not None:
        eligibility["cost_sharing"] = bool(block.get("costSharing"))
    for key in ("estimatedFunding", "numberOfAwards"):
        value = _clean(block.get(key))
        if value and value.lower() != "none":
            eligibility[_snake(key)] = value
    return {k: v for k, v in eligibility.items() if v not in ([], None, "")}


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def map_contact(block: Mapping[str, Any]) -> list[Contact]:
    raw_name = _clean(block.get("agencyContactName"))
    name, title = None, None
    if raw_name:
        lines = [line.strip() for line in raw_name.splitlines() if line.strip()]
        name = lines[0] if lines else None
        title = lines[1] if len(lines) > 1 else None
    contact = Contact(
        name=name,
        title=title,
        email=_clean(block.get("agencyContactEmail")),
        phone=_clean(block.get("agencyContactPhone")),
        kind="grantor",
    )
    return [contact] if (contact.name or contact.email or contact.phone) else []


def normalize_grants(
    hit: Mapping[str, Any] | None, detail: Mapping[str, Any] | None = None
) -> OpportunityIn:
    hit = hit or {}
    detail = detail or {}
    opp_id = _clean(detail.get("id")) or _clean(hit.get("id"))
    if not opp_id:
        raise ValueError("Grants.gov record without an opportunity id")
    block = _body(detail)
    forecast = is_forecast(hit, detail or None)
    number = _clean(detail.get("opportunityNumber")) or _clean(hit.get("number"))
    title = (
        _clean(detail.get("opportunityTitle"))
        or _clean(hit.get("title"))
        or f"Grants.gov opportunity {opp_id}"
    )
    top = _mapping(detail.get("topAgencyDetails"))
    agency = _mapping(detail.get("agencyDetails"))
    top_name = _clean(top.get("agencyName"))
    agency_name = _clean(agency.get("agencyName")) or _clean(hit.get("agency"))
    hierarchy = [n for n in (top_name, agency_name) if n]
    if len(hierarchy) == 2 and hierarchy[0] == hierarchy[1]:
        hierarchy = hierarchy[:1]
    aln = [c for c in (_clean(x.get("cfdaNumber")) for x in detail.get("cfdas") or []) if c] or [
        c for c in (_clean(x) for x in hit.get("cfdaList") or []) if c
    ]
    opp_status = (_clean(hit.get("oppStatus")) or _clean(detail.get("ost")) or "").lower()
    status = OpportunityStatus.CLOSED if opp_status in {"closed", "archived"} else None
    description = html_to_text(
        _clean(block.get("synopsisDesc")) or _clean(block.get("forecastDesc"))
    )
    posted = _first_date(block, "postingDateStr", "postingDate") or parse_grants_datetime(
        _clean(hit.get("openDate"))
    )
    due = _first_date(
        block,
        "responseDateStr",
        "responseDate",
        "estApplicationResponseDateStr",
        "estApplicationResponseDate",
    ) or parse_grants_datetime(_clean(hit.get("closeDate")))
    extra: dict[str, Any] = {
        "opp_status": opp_status or None,
        "doc_type": _clean(detail.get("docType")) or _clean(hit.get("docType")),
        "revision": detail.get("revision"),
        "agency_code": _clean(agency.get("agencyCode")) or _clean(hit.get("agencyCode")),
        "top_agency_code": _clean(top.get("agencyCode")),
        "category": (
            _clean(detail["opportunityCategory"].get("description"))
            if isinstance(detail.get("opportunityCategory"), Mapping)
            else None
        ),
        "links": [
            {"url": u, "description": _clean(x.get("description"))}
            for x in detail.get("synopsisDocumentURLs") or []
            if (u := _clean(x.get("docUrl")))
        ]
        or None,
        "funding_link": _clean(block.get("fundingDescLinkUrl")),
        "mod_comments": _clean(block.get("modComments")),
        "est_synopsis_posting_at": (
            _first_date(block, "estSynopsisPostingDateStr", "estSynopsisPostingDate") or None
        ),
        "est_award_at": _first_date(block, "estAwardDateStr", "estAwardDate") or None,
        "fiscal_year": block.get("fiscalYear"),
    }
    return OpportunityIn(
        source_id=SOURCE_ID,
        external_id=opp_id,
        source_url=DETAIL_URL.format(id=opp_id),
        region=Region.US,
        country="US",
        currency="USD",
        notice_type=NoticeType.FORECAST if forecast else NoticeType.GRANT,
        title=title,
        description_text=description,
        solicitation_number=number,
        buyer_org=hierarchy[0] if hierarchy else None,
        buyer_sub_org=hierarchy[1] if len(hierarchy) > 1 else None,
        buyer_hierarchy=hierarchy,
        aln=aln,
        estimated_value_min=parse_money(block.get("awardFloor")),
        estimated_value_max=parse_money(block.get("awardCeiling")),
        posted_at=posted,
        response_due_at=due,
        archive_at=_first_date(block, "archiveDateStr", "archiveDate"),
        source_tz=GRANTS_SOURCE_TZ,
        contacts=map_contact(block),
        eligibility=map_eligibility(block),
        documents=map_documents(detail),
        status=status,
        detail_status=DetailStatus.FULL if block else DetailStatus.PENDING,
        extra={
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in extra.items()
            if v is not None
        },
    )
