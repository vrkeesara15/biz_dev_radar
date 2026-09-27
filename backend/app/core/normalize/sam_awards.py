"""Pure mapping for SAM.gov contract awards (SPEC 2 row 2, 5.2) and the enrichment rules.

The awards search is the successor of the retired ATOM feed; the exact field names of
the live endpoint are absorbed by `ALIASES` (data, not code). Matching an award to an
opportunity: normalised solicitation number first, else NAICS + buyer agency. Contracts
ending 6-18 months out are recompete-watch candidates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import Region
from app.core.normalize.sam import normalized_solicitation, sam_date
from app.core.normalize.usaspending import is_recompete_candidate
from app.core.opportunity import (
    DetailStatus,
    NoticeType,
    OpportunityIn,
    OpportunityStatus,
)

SOURCE_ID = "sam_awards"
SAM_TZ = "America/New_York"
MAX_WINDOW = timedelta(days=365)

# canonical name -> accepted source keys (first present wins)
ALIASES: dict[str, tuple[str, ...]] = {
    "award_id": ("awardId", "award_id", "contractId", "id"),
    "piid": ("piid", "PIID", "awardNumber", "contractNumber"),
    "modification": ("modificationNumber", "modNumber", "modification_number"),
    "solicitation_number": ("solicitationNumber", "solicitationId", "solicitation_number"),
    "award_type": ("awardType", "contractActionType", "award_type"),
    "signed_date": ("signedDate", "dateSigned", "signed_date"),
    "start_date": ("effectiveDate", "periodOfPerformanceStartDate", "startDate"),
    "end_date": (
        "ultimateCompletionDate",
        "currentCompletionDate",
        "periodOfPerformanceEndDate",
        "endDate",
    ),
    "obligated": ("obligatedAmount", "dollarsObligated", "obligated_amount"),
    "total_value": ("baseAndAllOptionsValue", "totalContractValue", "base_and_all_options_value"),
    "num_offers": ("numberOfOffersReceived", "offersReceived", "number_of_offers"),
    "extent_competed": ("extentCompeted", "extent_competed"),
    "set_aside": ("typeOfSetAside", "setAsideCode", "set_aside"),
    "naics": ("naicsCode", "naics", "principalNaicsCode"),
    "psc": ("productOrServiceCode", "pscCode", "psc", "classificationCode"),
    "agency": ("agencyName", "contractingAgencyName", "agency"),
    "department": ("departmentName", "contractingDepartmentName", "department"),
    "office": ("contractingOfficeName", "contractingOffice", "office"),
    "description": ("descriptionOfRequirement", "description", "title"),
    "ui_link": ("uiLink", "url", "link"),
}


def _pick(record: Mapping[str, Any], name: str) -> Any:
    for key in ALIASES[name]:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() == "null" else text


def _date(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19] if "T" in text else text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _money(value: Any) -> Decimal | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return Decimal(text.replace(",", "").replace("$", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _int(value: Any) -> int | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ContractAward:
    award_id: str
    piid: str
    modification: str | None
    solicitation_number: str | None
    award_type: str | None
    signed_date: date | None
    start_date: date | None
    end_date: date | None
    obligated: Decimal | None
    total_value: Decimal | None
    num_offers: int | None
    extent_competed: str | None
    set_aside: str | None
    naics: str | None
    psc: str | None
    agency: str | None
    department: str | None
    office: str | None
    vendor_name: str | None
    vendor_uei: str | None
    description: str | None
    ui_link: str | None

    @property
    def value(self) -> Decimal | None:
        return self.total_value or self.obligated

    @property
    def hierarchy(self) -> list[str]:
        return [p for p in (self.department, self.agency, self.office) if p]

    @property
    def source_url(self) -> str:
        return self.ui_link or f"https://sam.gov/awards/{self.piid}/view"


def award_from_record(record: Mapping[str, Any]) -> ContractAward:
    piid = _clean(_pick(record, "piid")) or _clean(_pick(record, "award_id"))
    if not piid:
        raise ValueError("award without a PIID / award id")
    modification = _clean(_pick(record, "modification"))
    award_id = _clean(_pick(record, "award_id")) or (
        f"{piid}-{modification}" if modification else piid
    )
    raw_vendor = record.get("vendor")
    vendor: Mapping[str, Any] = raw_vendor if isinstance(raw_vendor, Mapping) else {}
    vendor_name = _clean(vendor.get("legalBusinessName")) or _clean(vendor.get("name"))
    if vendor_name is None:
        vendor_name = _clean(record.get("vendorName")) or _clean(record.get("recipientName"))
    return ContractAward(
        award_id=award_id,
        piid=piid,
        modification=modification,
        solicitation_number=_clean(_pick(record, "solicitation_number")),
        award_type=_clean(_pick(record, "award_type")),
        signed_date=_date(_pick(record, "signed_date")),
        start_date=_date(_pick(record, "start_date")),
        end_date=_date(_pick(record, "end_date")),
        obligated=_money(_pick(record, "obligated")),
        total_value=_money(_pick(record, "total_value")),
        num_offers=_int(_pick(record, "num_offers")),
        extent_competed=_clean(_pick(record, "extent_competed")),
        set_aside=_clean(_pick(record, "set_aside")),
        naics=_clean(_pick(record, "naics")),
        psc=_clean(_pick(record, "psc")),
        agency=_clean(_pick(record, "agency")),
        department=_clean(_pick(record, "department")),
        office=_clean(_pick(record, "office")),
        vendor_name=vendor_name,
        vendor_uei=_clean(vendor.get("ueiSAM")) or _clean(vendor.get("uei")),
        description=_clean(_pick(record, "description")),
        ui_link=_clean(_pick(record, "ui_link")),
    )


def award_search_params(
    naics: Sequence[str], start: datetime, end: datetime, *, limit: int, offset: int
) -> dict[str, Any]:
    if end - start > MAX_WINDOW:
        raise ValueError("awards search windows must not exceed one year")
    return {
        "naics": ",".join(naics),
        "signedDateFrom": sam_date(start),
        "signedDateTo": sam_date(end),
        "limit": limit,
        "offset": offset,
    }


def recompete_watch(award: ContractAward, now: datetime | date) -> bool:
    return is_recompete_candidate(award.end_date, now)


# --- matching ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """The few opportunity fields matching needs (id is opaque to core)."""

    id: Any
    solicitation_number: str | None
    naics: Sequence[str]
    buyer_org: str | None
    buyer_hierarchy: Sequence[str]
    posted_at: datetime | None = None


def _norm_agency(value: str | None) -> str:
    return " ".join((value or "").upper().replace(",", " ").split())


def agency_matches(award: ContractAward, candidate: Candidate) -> bool:
    names = {_norm_agency(n) for n in award.hierarchy if n}
    targets = {
        _norm_agency(candidate.buyer_org),
        *(_norm_agency(h) for h in candidate.buyer_hierarchy),
    }
    targets.discard("")
    return bool(names & targets)


def match_award(
    award: ContractAward, candidates: Iterable[Candidate]
) -> tuple[Candidate, str] | None:
    """Best opportunity for an award: solicitation number (normalised) beats NAICS + agency;
    ties go to the most recently posted candidate."""
    pool = list(candidates)
    key = normalized_solicitation(award.solicitation_number)
    if key:
        by_number = [c for c in pool if normalized_solicitation(c.solicitation_number) == key]
        if by_number:
            return _latest(by_number), "solicitation_number"
    if award.naics:
        by_codes = [c for c in pool if award.naics in c.naics and agency_matches(award, c)]
        if by_codes:
            return _latest(by_codes), "naics_agency"
    return None


def _latest(candidates: list[Candidate]) -> Candidate:
    floor = datetime.min.replace(tzinfo=UTC)
    return max(candidates, key=lambda c: c.posted_at or floor)


def award_to_opportunity(award: ContractAward) -> OpportunityIn:
    """Contract-conformant view (notice_type award); the enrichment job is the real consumer."""
    hierarchy = award.hierarchy
    signed = award.signed_date or award.start_date
    return OpportunityIn(
        source_id=SOURCE_ID,
        external_id=award.award_id,
        source_url=award.source_url,
        region=Region.US,
        country="US",
        currency="USD",
        notice_type=NoticeType.AWARD,
        title=award.description or f"Award {award.piid}",
        solicitation_number=award.solicitation_number,
        buyer_org=hierarchy[0] if hierarchy else None,
        buyer_sub_org=hierarchy[1] if len(hierarchy) > 1 else None,
        buyer_office=hierarchy[2] if len(hierarchy) > 2 else None,
        buyer_hierarchy=hierarchy,
        naics=[award.naics] if award.naics else [],
        psc=[award.psc] if award.psc else [],
        set_aside=award.set_aside.upper() if award.set_aside else None,
        estimated_value_max=award.value,
        posted_at=(
            datetime.combine(signed, datetime.min.time(), tzinfo=ZoneInfo(SAM_TZ))
            if signed
            else None
        ),
        source_tz=SAM_TZ,
        status=OpportunityStatus.AWARDED,
        detail_status=DetailStatus.FULL,
        extra={
            "piid": award.piid,
            "modification": award.modification,
            "vendor": award.vendor_name,
            "vendor_uei": award.vendor_uei,
            "num_offers": award.num_offers,
            "pop_start": award.start_date.isoformat() if award.start_date else None,
            "pop_end": award.end_date.isoformat() if award.end_date else None,
            "extent_competed": award.extent_competed,
            "award_type": award.award_type,
        },
    )
