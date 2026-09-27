"""Pure mapping from SAM.gov Get Opportunities v2 records to OpportunityIn (SPEC 5.2, 5.3).

Type codes (ptype) -> notice_type:
    p  Presolicitation                          -> presolicitation
    o  Solicitation                             -> rfp
    k  Combined Synopsis/Solicitation           -> combined
    r  Sources Sought                           -> sources_sought
    s  Special Notice                           -> special
    i  Intent to Bundle Requirements (DoD)      -> special
    a  Award Notice                             -> award
    u  Justification (J&A)                      -> special
    g  Sale of Surplus Property                 -> special
Retired codes f (Foreign Government Standard) and l (Fair Opportunity / Limited Sources)
map to special as well when they appear in archived data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import Region
from app.core.opportunity import (
    Contact,
    DetailStatus,
    DocumentKind,
    DocumentRef,
    NoticeType,
    OpportunityIn,
    OpportunityStatus,
    PlaceOfPerformance,
)

SOURCE_ID = "sam_opps"
# Federal notices publish deadlines in the office's zone; SAM renders them in Eastern time.
SAM_SOURCE_TZ = "America/New_York"
SAM_DATE_FORMAT = "%m/%d/%Y"
MAX_WINDOW = timedelta(days=365)

NOTICE_TYPE_BY_CODE: dict[str, NoticeType] = {
    "p": NoticeType.PRESOLICITATION,
    "o": NoticeType.RFP,
    "k": NoticeType.COMBINED,
    "r": NoticeType.SOURCES_SOUGHT,
    "s": NoticeType.SPECIAL,
    "i": NoticeType.SPECIAL,
    "a": NoticeType.AWARD,
    "u": NoticeType.SPECIAL,
    "g": NoticeType.SPECIAL,
    "f": NoticeType.SPECIAL,
    "l": NoticeType.SPECIAL,
}

# The API returns the type as a display name; map those back onto the codes above.
TYPE_NAME_TO_CODE: dict[str, str] = {
    "presolicitation": "p",
    "solicitation": "o",
    "combined synopsis/solicitation": "k",
    "sources sought": "r",
    "special notice": "s",
    "intent to bundle requirements (dod-funded)": "i",
    "intent to bundle requirements (dod- funded)": "i",
    "award notice": "a",
    "justification": "u",
    "justification (j&a)": "u",
    "justification and approval (j&a)": "u",
    "sale of surplus property": "g",
    "foreign government standard": "f",
    "fair opportunity / limited sources justification": "l",
    "fair opportunity / limited sources": "l",
}


def type_code(value: str | None) -> str | None:
    """Accept a one-letter ptype code or a display name; return the code or None."""
    if not value:
        return None
    text = value.strip().lower()
    if len(text) == 1:
        return text if text in NOTICE_TYPE_BY_CODE else None
    return TYPE_NAME_TO_CODE.get(text)


def notice_type_for(value: str | None) -> NoticeType:
    code = type_code(value)
    return NOTICE_TYPE_BY_CODE.get(code or "", NoticeType.SPECIAL)


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_sam_datetime(value: str | None, *, tz: str = SAM_SOURCE_TZ) -> datetime | None:
    """Parse SAM date strings into aware UTC datetimes.

    Seen formats: '2026-09-25', '2026-09-25 13:00:00', '2026-09-25T13:00:00-04:00',
    '2026-09-25T13:00:00Z', '09/25/2026'. Naive values are interpreted in `tz`.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    parsed: datetime | None = None
    if _ISO_RE.match(text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    else:
        for fmt in (SAM_DATE_FORMAT, "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M %p"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.astimezone(UTC)


def sam_date(moment: datetime) -> str:
    """MM/dd/yyyy in Eastern time, the format postedFrom/postedTo require."""
    return moment.astimezone(ZoneInfo(SAM_SOURCE_TZ)).strftime(SAM_DATE_FORMAT)


def sam_window_params(start: datetime, end: datetime) -> dict[str, str]:
    """postedFrom/postedTo for one fetch window; the caller keeps windows <= 1 year."""
    if end - start > MAX_WINDOW:
        raise ValueError("SAM.gov rejects postedFrom/postedTo spans over one year")
    return {"postedFrom": sam_date(start), "postedTo": sam_date(end)}


def split_hierarchy(path: str | None) -> list[str]:
    """'DEPT OF DEFENSE.DEPT OF THE ARMY.AMC.ACC.ACC-APG' -> list of levels."""
    if not path:
        return []
    return [part.strip() for part in path.split(".") if part.strip()]


def _clean(value: Any) -> str | None:
    """Trimmed string or None; SAM sometimes serialises missing values as the string 'null'."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _name(node: Any) -> str | None:
    """SAM nests city/state/country as {code, name}; accept plain strings too."""
    if isinstance(node, Mapping):
        return _clean(node.get("name")) or _clean(node.get("code"))
    return _clean(node)


def _code(node: Any) -> str | None:
    """Prefer the short code (state 'NC') over the display name for matching."""
    if isinstance(node, Mapping):
        return _clean(node.get("code")) or _clean(node.get("name"))
    return _clean(node)


def map_place_of_performance(node: Mapping[str, Any] | None) -> PlaceOfPerformance | None:
    if not node:
        return None
    country = _name(node.get("country"))
    if country and country.upper() in {"USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        country = "US"
    return PlaceOfPerformance(
        city=_name(node.get("city")),
        state=_code(node.get("state")),
        country=country,
        postal_code=_clean(node.get("zip")),
        raw=_clean(node.get("streetAddress")),
    )


def map_contacts(nodes: Iterable[Mapping[str, Any]] | None) -> list[Contact]:
    contacts: list[Contact] = []
    for node in nodes or []:
        contact = Contact(
            name=_clean(node.get("fullName")) or _clean(node.get("fullname")),
            title=_clean(node.get("title")),
            email=_clean(node.get("email")),
            phone=_clean(node.get("phone")),
            kind=_clean(node.get("type")),
        )
        if any((contact.name, contact.email, contact.phone)):
            contacts.append(contact)
    return contacts


def _file_name_from_url(url: str) -> str | None:
    tail = url.rsplit("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return tail if "." in tail else None


def map_resource_links(record: Mapping[str, Any]) -> list[DocumentRef]:
    links = record.get("resourceLinks") or []
    refs: list[DocumentRef] = []
    seen: set[str] = set()
    for url in links:
        if not isinstance(url, str) or not url.strip() or url in seen:
            continue
        seen.add(url)
        refs.append(
            DocumentRef(url=url, file_name=_file_name_from_url(url), kind=DocumentKind.ATTACHMENT)
        )
    return refs


def _naics(record: Mapping[str, Any]) -> list[str]:
    codes: list[str] = []
    for value in [record.get("naicsCode"), *(record.get("naicsCodes") or [])]:
        code = _clean(value)
        if code and code not in codes:
            codes.append(code)
    return codes


def status_from_record(
    record: Mapping[str, Any], notice_type: NoticeType
) -> OpportunityStatus | None:
    """Only what the source states: awards are awarded; archived notices are closed."""
    if notice_type is NoticeType.AWARD:
        return OpportunityStatus.AWARDED
    active = _clean(record.get("active"))
    if active and active.lower() == "no":
        return OpportunityStatus.CLOSED
    return None


def normalize_sam_notice(
    record: Mapping[str, Any], *, description_text: str | None = None
) -> OpportunityIn:
    notice_id = _clean(record.get("noticeId"))
    if not notice_id:
        raise ValueError("SAM record without noticeId")
    title = _clean(record.get("title")) or f"SAM.gov notice {notice_id}"
    hierarchy = split_hierarchy(record.get("fullParentPathName"))
    if not hierarchy:
        hierarchy = [
            level
            for level in (
                _clean(record.get("department")),
                _clean(record.get("subTier")),
                _clean(record.get("office")),
            )
            if level
        ]
    ntype = notice_type_for(_clean(record.get("type")))
    base_code = type_code(_clean(record.get("baseType")))
    set_aside = _clean(record.get("typeOfSetAside")) or _clean(record.get("setAsideCode"))
    award = record.get("award") if isinstance(record.get("award"), Mapping) else None
    extra: dict[str, Any] = {
        "type": _clean(record.get("type")),
        "type_code": type_code(_clean(record.get("type"))),
        "base_type": _clean(record.get("baseType")),
        "base_type_code": base_code,
        "archive_type": _clean(record.get("archiveType")),
        "set_aside_description": _clean(record.get("typeOfSetAsideDescription"))
        or _clean(record.get("setAside")),
        "classification_code": _clean(record.get("classificationCode")),
        "organization_type": _clean(record.get("organizationType")),
        "office_address": record.get("officeAddress") or None,
        "description_url": _clean(record.get("description")),
        "additional_info_link": _clean(record.get("additionalInfoLink")),
        "active": _clean(record.get("active")),
    }
    if award:
        extra["award"] = dict(award)
    return OpportunityIn(
        source_id=SOURCE_ID,
        external_id=notice_id,
        source_url=_clean(record.get("uiLink")) or f"https://sam.gov/opp/{notice_id}/view",
        region=Region.US,
        country="US",
        currency="USD",
        notice_type=ntype,
        title=title,
        description_text=description_text,
        solicitation_number=_clean(record.get("solicitationNumber")),
        buyer_org=hierarchy[0] if hierarchy else None,
        buyer_sub_org=hierarchy[1] if len(hierarchy) > 1 else None,
        buyer_office=hierarchy[-1] if len(hierarchy) > 2 else None,
        buyer_hierarchy=hierarchy,
        naics=_naics(record),
        psc=[c for c in [_clean(record.get("classificationCode"))] if c],
        set_aside=set_aside.upper() if set_aside else None,
        place_of_performance=map_place_of_performance(record.get("placeOfPerformance")),
        posted_at=parse_sam_datetime(_clean(record.get("postedDate"))),
        response_due_at=parse_sam_datetime(
            _clean(record.get("responseDeadLine")) or _clean(record.get("reponseDeadLine"))
        ),
        archive_at=parse_sam_datetime(_clean(record.get("archiveDate"))),
        source_tz=SAM_SOURCE_TZ,
        contacts=map_contacts(record.get("pointOfContact")),
        documents=map_resource_links(record),
        status=status_from_record(record, ntype),
        detail_status=DetailStatus.FULL if description_text is not None else DetailStatus.PENDING,
        extra={k: v for k, v in extra.items() if v is not None},
    )


def link_amendments(records: Iterable[OpportunityIn]) -> list[OpportunityIn]:
    """Within a batch, point every later notice with the same solicitationNumber at the
    earliest one (parent_external_id). The pipeline resolves it to parent_opportunity_id
    and also looks up earlier notices already in the database."""
    items = list(records)
    earliest: dict[str, OpportunityIn] = {}
    for opp in items:
        key = normalized_solicitation(opp.solicitation_number)
        if not key:
            continue
        current = earliest.get(key)
        if current is None or _sort_key(opp) < _sort_key(current):
            earliest[key] = opp
    linked: list[OpportunityIn] = []
    for opp in items:
        key = normalized_solicitation(opp.solicitation_number)
        parent = earliest.get(key or "")
        if parent is not None and parent.external_id != opp.external_id:
            linked.append(opp.model_copy(update={"parent_external_id": parent.external_id}))
        else:
            linked.append(opp)
    return linked


def _sort_key(opp: OpportunityIn) -> tuple[datetime, str]:
    return (opp.posted_at or datetime.max.replace(tzinfo=UTC), opp.external_id)


def normalized_solicitation(value: str | None) -> str | None:
    """Uppercase, strip whitespace and punctuation so ' 47PF0018R0023 ' == '47pf-0018-r0023'."""
    if not value:
        return None
    key = re.sub(r"[^A-Z0-9]", "", value.upper())
    return key or None
