"""Pure USAspending helpers (SPEC 2 row 3, 5.2): award rows -> AwardRecord, US federal
fiscal years, spend aggregation by agency x NAICS x PSC, recompete window, request body.

USAspending awards are not opportunities; the adapter still emits OpportunityIn rows
(notice_type award, status awarded) to satisfy the adapter contract, while the weekly job
consumes `AwardRecord`s for statistics and recompete candidates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.config import Region
from app.core.opportunity import (
    DetailStatus,
    NoticeType,
    OpportunityIn,
    OpportunityStatus,
)

SOURCE_ID = "usaspending"
AWARD_URL = "https://www.usaspending.gov/award/{id}"
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]  # BPA call, purchase order, delivery order, definitive
FIELDS = [
    "Award ID",
    "Recipient Name",
    "Award Amount",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Start Date",
    "End Date",
    "NAICS",
    "PSC",
    "Description",
    "Last Modified Date",
    "generated_internal_id",
    "recipient_id",
]
RECOMPETE_MIN = timedelta(days=182)  # ~6 months
RECOMPETE_MAX = timedelta(days=548)  # ~18 months
DOD_LAG_NOTE = "DoD data lags ~90 days on USAspending (SPEC 2 row 3)"


@dataclass(frozen=True, slots=True)
class AwardRecord:
    award_id: str
    generated_internal_id: str | None
    recipient: str | None
    amount: Decimal
    agency: str
    sub_agency: str
    start_date: date | None
    end_date: date | None
    naics: str
    naics_description: str | None
    psc: str
    psc_description: str | None
    description: str | None
    last_modified: datetime | None

    @property
    def url(self) -> str | None:
        return (
            AWARD_URL.format(id=self.generated_internal_id) if self.generated_internal_id else None
        )


def fiscal_year(day: date) -> int:
    """US federal fiscal year: Oct 1 - Sep 30, named for the calendar year it ends in."""
    return day.year + 1 if day.month >= 10 else day.year


def fiscal_year_start(fy: int) -> date:
    return date(fy - 1, 10, 1)


def fiscal_year_end(fy: int) -> date:
    return date(fy, 9, 30)


def last_fiscal_years(now: datetime | date, count: int = 3) -> list[int]:
    """The current fiscal year and the `count - 1` before it, ascending."""
    today = now.date() if isinstance(now, datetime) else now
    current = fiscal_year(today)
    return list(range(current - count + 1, current + 1))


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0")


def _code(node: Any) -> tuple[str, str | None]:
    if isinstance(node, Mapping):
        code = str(node.get("code") or "").strip()
        desc = node.get("description")
        return code, (str(desc).strip() or None) if desc else None
    if isinstance(node, str):
        return node.strip(), None
    return "", None


def award_from_row(row: Mapping[str, Any]) -> AwardRecord:
    award_id = str(row.get("Award ID") or "").strip()
    if not award_id:
        raise ValueError("USAspending row without Award ID")
    naics, naics_desc = _code(row.get("NAICS"))
    psc, psc_desc = _code(row.get("PSC"))
    recipient = row.get("Recipient Name")
    return AwardRecord(
        award_id=award_id,
        generated_internal_id=(
            str(row["generated_internal_id"]) if row.get("generated_internal_id") else None
        ),
        recipient=str(recipient).strip() if recipient else None,
        amount=_money(row.get("Award Amount")),
        agency=str(row.get("Awarding Agency") or "").strip(),
        sub_agency=str(row.get("Awarding Sub Agency") or "").strip(),
        start_date=_parse_date(row.get("Start Date")),
        end_date=_parse_date(row.get("End Date")),
        naics=naics,
        naics_description=naics_desc,
        psc=psc,
        psc_description=psc_desc,
        description=(str(row["Description"]).strip() or None) if row.get("Description") else None,
        last_modified=_parse_datetime(row.get("Last Modified Date")),
    )


SpendKey = tuple[str, str, str, str, int]


@dataclass(slots=True)
class SpendBucket:
    obligations: Decimal
    award_count: int


def aggregate_spend(
    awards: Iterable[AwardRecord], fiscal_years: Iterable[int] | None = None
) -> dict[SpendKey, SpendBucket]:
    """Sum obligations and count awards per (agency, sub_agency, naics, psc, fiscal_year).

    The fiscal year is the award's start date FY (fallback: end date FY). Awards outside
    `fiscal_years` (when given) or without any date are skipped.
    """
    wanted = set(fiscal_years) if fiscal_years is not None else None
    buckets: dict[SpendKey, SpendBucket] = defaultdict(lambda: SpendBucket(Decimal("0"), 0))
    for award in awards:
        anchor = award.start_date or award.end_date
        if anchor is None:
            continue
        fy = fiscal_year(anchor)
        if wanted is not None and fy not in wanted:
            continue
        key: SpendKey = (award.agency, award.sub_agency, award.naics, award.psc, fy)
        bucket = buckets[key]
        bucket.obligations += award.amount
        bucket.award_count += 1
    return dict(buckets)


def is_recompete_candidate(end_date: date | None, now: datetime | date) -> bool:
    """Period of performance ends 6-18 months from now (SPEC 5.2 recompete watch)."""
    if end_date is None:
        return False
    today = now.date() if isinstance(now, datetime) else now
    delta = end_date - today
    return RECOMPETE_MIN <= delta <= RECOMPETE_MAX


def spending_by_award_body(
    start: date,
    end: date,
    *,
    page: int,
    limit: int,
    naics_codes: list[str] | None = None,
    agencies: list[str] | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        "award_type_codes": CONTRACT_TYPE_CODES,
    }
    if naics_codes:
        filters["naics_codes"] = {"require": list(naics_codes)}
    if agencies:
        filters["agencies"] = [
            {"type": "awarding", "tier": "toptier", "name": name} for name in agencies
        ]
    return {
        "filters": filters,
        "fields": FIELDS,
        "page": page,
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
        "subawards": False,
    }


def award_to_opportunity(award: AwardRecord) -> OpportunityIn:
    """Contract-conformant view of an award (notice_type award, status awarded)."""
    hierarchy = [p for p in (award.agency, award.sub_agency) if p]
    if len(hierarchy) == 2 and hierarchy[0] == hierarchy[1]:
        hierarchy = hierarchy[:1]
    title = award.description or f"Award {award.award_id}"
    return OpportunityIn(
        source_id=SOURCE_ID,
        external_id=award.generated_internal_id or award.award_id,
        source_url=award.url,
        region=Region.US,
        country="US",
        currency="USD",
        notice_type=NoticeType.AWARD,
        title=title[:500],
        solicitation_number=award.award_id,
        buyer_org=hierarchy[0] if hierarchy else None,
        buyer_sub_org=hierarchy[1] if len(hierarchy) > 1 else None,
        buyer_hierarchy=hierarchy,
        naics=[award.naics] if award.naics else [],
        psc=[award.psc] if award.psc else [],
        estimated_value_max=award.amount,
        posted_at=(
            datetime.combine(award.start_date, datetime.min.time(), tzinfo=UTC)
            if award.start_date
            else None
        ),
        status=OpportunityStatus.AWARDED,
        detail_status=DetailStatus.FULL,
        extra={
            "award_id": award.award_id,
            "recipient": award.recipient,
            "pop_start": award.start_date.isoformat() if award.start_date else None,
            "pop_end": award.end_date.isoformat() if award.end_date else None,
            "naics_description": award.naics_description,
            "psc_description": award.psc_description,
        },
    )
