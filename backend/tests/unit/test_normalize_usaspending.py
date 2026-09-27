"""M2-07: pure USAspending helpers over the recorded fixture."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.normalize.usaspending import (
    CONTRACT_TYPE_CODES,
    FIELDS,
    aggregate_spend,
    award_from_row,
    award_to_opportunity,
    fiscal_year,
    fiscal_year_end,
    fiscal_year_start,
    is_recompete_candidate,
    last_fiscal_years,
    spending_by_award_body,
)
from app.core.opportunity import NoticeType, OpportunityStatus

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "usaspending"
ROWS = [
    *json.loads((FIXTURES / "spending_by_award_page1.json").read_text())["results"],
    *json.loads((FIXTURES / "spending_by_award_page2.json").read_text())["results"],
]
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def test_fiscal_year_math() -> None:
    assert fiscal_year(date(2026, 9, 30)) == 2026
    assert fiscal_year(date(2026, 10, 1)) == 2027
    assert fiscal_year_start(2027) == date(2026, 10, 1)
    assert fiscal_year_end(2027) == date(2027, 9, 30)
    assert last_fiscal_years(NOW) == [2024, 2025, 2026]
    assert last_fiscal_years(date(2026, 10, 2), count=2) == [2026, 2027]


def test_award_from_row_maps_real_columns() -> None:
    award = award_from_row(ROWS[0])
    assert award.award_id == ROWS[0]["Award ID"]
    assert award.recipient == ROWS[0]["Recipient Name"]
    assert award.amount == Decimal(str(ROWS[0]["Award Amount"])).quantize(Decimal("0.01"))
    assert award.agency == ROWS[0]["Awarding Agency"]
    assert award.naics == ROWS[0]["NAICS"]["code"] and award.psc == ROWS[0]["PSC"]["code"]
    assert award.naics_description == ROWS[0]["NAICS"]["description"]
    assert award.start_date == date.fromisoformat(ROWS[0]["Start Date"])
    assert award.end_date == date.fromisoformat(ROWS[0]["End Date"])
    assert award.generated_internal_id == ROWS[0]["generated_internal_id"]
    assert award.url == f"https://www.usaspending.gov/award/{ROWS[0]['generated_internal_id']}"
    assert award.last_modified is not None and award.last_modified.tzinfo is UTC
    sparse = award_from_row({"Award ID": "X1", "Award Amount": None, "NAICS": None, "PSC": "R425"})
    assert sparse.amount == 0 and sparse.naics == "" and sparse.psc == "R425"
    assert sparse.url is None and sparse.start_date is None
    with pytest.raises(ValueError):
        award_from_row({"Recipient Name": "nobody"})


def test_aggregate_spend_by_agency_naics_psc_fiscal_year() -> None:
    awards = [award_from_row(r) for r in ROWS]
    buckets = aggregate_spend(awards, [2022, 2023, 2024, 2025, 2026])
    assert buckets, "fixture rows fall in the covered fiscal years"
    total = sum(b.award_count for b in buckets.values())
    dated = [a for a in awards if (a.start_date or a.end_date)]
    kept = [a for a in dated if fiscal_year(a.start_date or a.end_date) in range(2022, 2027)]  # type: ignore[arg-type]
    assert total == len(kept)
    first = awards[0]
    key = (first.agency, first.sub_agency, first.naics, first.psc, fiscal_year(first.start_date))  # type: ignore[arg-type]
    expected = sum(
        a.amount
        for a in awards
        if (a.agency, a.sub_agency, a.naics, a.psc, fiscal_year(a.start_date or a.end_date)) == key  # type: ignore[arg-type]
    )
    assert buckets[key].obligations == expected
    # restricting the window drops out-of-range awards; no window keeps all dated awards
    assert sum(b.award_count for b in aggregate_spend(awards).values()) == len(dated)
    assert aggregate_spend(awards, [1999]) == {}


def test_recompete_window_is_six_to_eighteen_months() -> None:
    assert not is_recompete_candidate(None, NOW)
    assert not is_recompete_candidate(date(2026, 12, 1), NOW)  # ~2 months
    assert is_recompete_candidate(date(2027, 3, 31), NOW)  # ~6 months
    assert is_recompete_candidate(date(2027, 12, 1), NOW)  # ~14 months
    assert is_recompete_candidate(date(2028, 3, 20), NOW)  # just under 18 months
    assert not is_recompete_candidate(date(2028, 6, 1), NOW)  # ~20 months
    assert not is_recompete_candidate(date(2025, 1, 1), NOW)  # already ended


def test_request_body_shape() -> None:
    body = spending_by_award_body(
        date(2023, 10, 1), date(2026, 9, 26), page=3, limit=100, naics_codes=["541512"]
    )
    assert body["filters"]["time_period"] == [
        {"start_date": "2023-10-01", "end_date": "2026-09-26"}
    ]
    assert body["filters"]["award_type_codes"] == CONTRACT_TYPE_CODES == ["A", "B", "C", "D"]
    assert body["filters"]["naics_codes"] == {"require": ["541512"]}
    assert body["fields"] == FIELDS and "Award ID" in FIELDS and "NAICS" in FIELDS
    assert body["page"] == 3 and body["limit"] == 100
    assert body["sort"] == "Award Amount" and body["order"] == "desc"
    assert (
        "naics_codes"
        not in spending_by_award_body(date(2023, 10, 1), date(2026, 9, 26), page=1, limit=100)[
            "filters"
        ]
    )
    with_agency = spending_by_award_body(
        date(2023, 10, 1), date(2026, 9, 26), page=1, limit=10, agencies=["Department of Defense"]
    )
    assert with_agency["filters"]["agencies"][0]["name"] == "Department of Defense"


def test_award_to_opportunity_is_contract_conformant() -> None:
    award = award_from_row(ROWS[0])
    opp = award_to_opportunity(award)
    assert opp.source_id == "usaspending"
    assert opp.notice_type is NoticeType.AWARD and opp.status is OpportunityStatus.AWARDED
    assert opp.external_id == award.generated_internal_id
    assert opp.solicitation_number == award.award_id
    assert opp.estimated_value_max == award.amount
    assert opp.naics == [award.naics] and opp.psc == [award.psc]
    assert opp.buyer_org == award.agency
    assert opp.extra["recipient"] == award.recipient
    assert opp.posted_at == datetime.combine(award.start_date, datetime.min.time(), tzinfo=UTC)  # type: ignore[arg-type]
