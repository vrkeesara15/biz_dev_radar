"""M2-08: pure award parsing, search params and matching rules."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.normalize.sam_awards import (
    Candidate,
    agency_matches,
    award_from_record,
    award_search_params,
    award_to_opportunity,
    match_award,
    recompete_watch,
)
from app.core.opportunity import NoticeType, OpportunityStatus

FIXTURE = (
    Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "sam_awards" / "page1.json"
)
ROWS = json.loads(FIXTURE.read_text())["awardsData"]
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[2]


def test_nothing_references_the_retired_feed() -> None:
    needle = "fp" + "ds"
    offenders = [
        str(p.relative_to(BACKEND))
        for p in (BACKEND / "app").rglob("*.py")
        if needle in p.read_text().lower()
    ]
    assert offenders == []


def test_award_from_record_maps_fields() -> None:
    award = award_from_record(ROWS[0])
    assert award.award_id == "W911NF20C0007-0" and award.piid == "W911NF20C0007"
    assert award.solicitation_number == "W911NF-26-R-0007"
    assert award.signed_date == date(2020, 9, 28)
    assert award.start_date == date(2020, 10, 1) and award.end_date == date(2027, 6, 30)
    assert award.obligated == Decimal("18450000.00")
    assert award.total_value == Decimal("24900000.00") == award.value
    assert award.num_offers == 5
    assert award.naics == "541512" and award.psc == "DA01"
    assert award.hierarchy == ["DEPT OF DEFENSE", "DEPT OF THE ARMY", "ACC-APG RTP DIV"]
    assert award.vendor_name == "Northwind Federal Systems LLC"
    assert award.vendor_uei == "ZQ1N8FAKEUEI"
    assert award.set_aside == "SBA"
    assert award.source_url == "https://sam.gov/awards/W911NF20C0007/view"
    assert award_from_record(ROWS[1]).solicitation_number is None


def test_aliases_absorb_renamed_fields() -> None:
    award = award_from_record(
        {
            "PIID": "ABC123",
            "modNumber": "P00001",
            "solicitationId": "SOL-1",
            "dateSigned": "09/01/2026",
            "periodOfPerformanceEndDate": "2028-01-31T00:00:00",
            "dollarsObligated": "1,000.50",
            "offersReceived": "4.0",
            "principalNaicsCode": "541511",
            "pscCode": "DA10",
            "contractingAgencyName": "AGENCY",
            "vendorName": "Vendor Co",
        }
    )
    assert award.award_id == "ABC123-P00001" and award.piid == "ABC123"
    assert award.signed_date == date(2026, 9, 1) and award.end_date == date(2028, 1, 31)
    assert award.obligated == Decimal("1000.50") and award.value == Decimal("1000.50")
    assert award.num_offers == 4 and award.vendor_name == "Vendor Co"
    assert award.agency == "AGENCY" and award.hierarchy == ["AGENCY"]
    with pytest.raises(ValueError):
        award_from_record({"vendor": {"legalBusinessName": "nobody"}})


def test_search_params_and_window_limit() -> None:
    start = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    params = award_search_params(["541512", "541519"], start, NOW, limit=100, offset=200)
    assert params == {
        "naics": "541512,541519",
        "signedDateFrom": "09/01/2026",
        "signedDateTo": "09/26/2026",
        "limit": 100,
        "offset": 200,
    }
    with pytest.raises(ValueError):
        award_search_params(["1"], NOW - timedelta(days=400), NOW, limit=1, offset=0)


def test_recompete_watch_window() -> None:
    awards = [award_from_record(r) for r in ROWS]
    assert [recompete_watch(a, NOW) for a in awards] == [True, False, True, False]


def _candidates() -> list[Candidate]:
    return [
        Candidate(
            id="opp-army",
            solicitation_number="W911NF-26-R-0007",
            naics=["541512"],
            buyer_org="DEPT OF DEFENSE",
            buyer_hierarchy=["DEPT OF DEFENSE", "DEPT OF THE ARMY", "AMC"],
            posted_at=datetime(2026, 9, 20, tzinfo=UTC),
        ),
        Candidate(
            id="opp-army-amend",
            solicitation_number="w911nf26r0007",
            naics=["541512"],
            buyer_org="DEPT OF DEFENSE",
            buyer_hierarchy=["DEPT OF DEFENSE"],
            posted_at=datetime(2026, 9, 24, tzinfo=UTC),
        ),
        Candidate(
            id="opp-gsa",
            solicitation_number="47QFCA26Q0042",
            naics=["541519"],
            buyer_org="GENERAL SERVICES ADMINISTRATION",
            buyer_hierarchy=["GENERAL SERVICES ADMINISTRATION", "FEDERAL ACQUISITION SERVICE"],
            posted_at=datetime(2026, 9, 22, tzinfo=UTC),
        ),
        Candidate(
            id="opp-other-541519",
            solicitation_number="X-1",
            naics=["541519"],
            buyer_org="DEPT OF ENERGY",
            buyer_hierarchy=["DEPT OF ENERGY"],
        ),
    ]


def test_match_by_solicitation_number_prefers_latest_posting() -> None:
    award = award_from_record(ROWS[0])
    matched = match_award(award, _candidates())
    assert matched is not None
    candidate, method = matched
    assert candidate.id == "opp-army-amend" and method == "solicitation_number"


def test_match_by_naics_and_agency() -> None:
    award = award_from_record(ROWS[1])  # no solicitation number, NAICS 541519, GSA/FAS
    matched = match_award(award, _candidates())
    assert matched is not None
    assert matched[0].id == "opp-gsa" and matched[1] == "naics_agency"
    assert agency_matches(award, _candidates()[2])
    assert not agency_matches(award, _candidates()[3])


def test_no_match_cases() -> None:
    assert match_award(award_from_record(ROWS[2]), _candidates()) is None  # NAICS 236220
    assert match_award(award_from_record(ROWS[3]), _candidates()) is None  # NAICS 541611
    assert match_award(award_from_record(ROWS[0]), []) is None


def test_award_to_opportunity_contract_view() -> None:
    opp = award_to_opportunity(award_from_record(ROWS[0]))
    assert opp.source_id == "sam_awards" and opp.notice_type is NoticeType.AWARD
    assert opp.status is OpportunityStatus.AWARDED
    assert opp.external_id == "W911NF20C0007-0"
    assert opp.buyer_office == "ACC-APG RTP DIV" and opp.set_aside == "SBA"
    assert opp.estimated_value_max == Decimal("24900000.00")
    assert opp.posted_at == datetime(2020, 9, 28, 4, 0, tzinfo=UTC)
    assert opp.extra["num_offers"] == 5 and opp.extra["vendor"].startswith("Northwind")
