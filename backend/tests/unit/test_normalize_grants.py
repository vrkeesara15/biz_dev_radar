"""M2-06: pure Grants.gov mapping over the recorded fixtures."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.normalize.grants import (
    date_range_for,
    is_forecast,
    keep_latest,
    normalize_grants,
    parse_grants_datetime,
    parse_money,
)
from app.core.opportunity import DetailStatus, NoticeType, OpportunityStatus

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "grants_gov"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text())


HITS = [
    *_load("search2_page1.json")["data"]["oppHits"],
    *_load("search2_page2.json")["data"]["oppHits"],
]
DETAIL = _load("fetch_332894.json")["data"]
FORECAST = _load("fetch_forecast_334905.json")["data"]


def test_parse_grants_datetime_formats() -> None:
    assert parse_grants_datetime("04/30/2027") == datetime(2027, 4, 30, 4, 0, tzinfo=UTC)
    assert parse_grants_datetime("Apr 30, 2027 12:00:00 AM EDT") == datetime(
        2027, 4, 30, 4, 0, tzinfo=UTC
    )
    assert parse_grants_datetime("Jan 15, 2027 12:00:00 AM EST") == datetime(
        2027, 1, 15, 5, 0, tzinfo=UTC
    )
    assert parse_grants_datetime("2027-04-30-00-00-00") == datetime(2027, 4, 30, 4, 0, tzinfo=UTC)
    assert parse_grants_datetime("Mar 18, 2026 03:45:10 PM EDT") == datetime(
        2026, 3, 18, 19, 45, 10, tzinfo=UTC
    )
    assert parse_grants_datetime("") is None and parse_grants_datetime(None) is None
    assert parse_grants_datetime("soon") is None


def test_parse_money_and_date_range() -> None:
    assert parse_money("none") is None and parse_money("") is None and parse_money(None) is None
    assert parse_money("2500000") == Decimal("2500000")
    assert parse_money("$1,250,000.50") == Decimal("1250000.50")
    assert parse_money(10) == Decimal("10")
    assert parse_money("abc") is None
    assert date_range_for(1) == "3" and date_range_for(3) == "3"
    assert date_range_for(3.5) == "7" and date_range_for(20) == "21" and date_range_for(56) == "56"
    assert date_range_for(57) is None


def test_keep_latest_prefers_posted_then_later_open_date() -> None:
    kept = keep_latest(HITS)
    numbers = [h["number"] for h in kept]
    assert len(numbers) == len(set(numbers)) == 7
    w911 = next(h for h in kept if h["number"] == "W911NF21S0009")
    assert w911["id"] == "332894" and w911["oppStatus"] == "posted"
    assert all(h["id"] != "999001" for h in kept)
    # order of first appearance kept
    assert numbers[0] == HITS[0]["number"]
    # same status: later open date wins; then higher id
    a = {"id": "1", "number": "X", "oppStatus": "forecasted", "openDate": "01/01/2026"}
    b = {"id": "2", "number": "X", "oppStatus": "forecasted", "openDate": "02/01/2026"}
    c = {"id": "3", "number": "X", "oppStatus": "forecasted", "openDate": "02/01/2026"}
    assert keep_latest([a, b])[0]["id"] == "2"
    assert keep_latest([c, b])[0]["id"] == "3"
    assert keep_latest([{"id": "9", "title": "no number"}])[0]["id"] == "9"


def test_search_hits_normalize_without_detail() -> None:
    opps = [normalize_grants(h) for h in HITS]
    assert {o.notice_type for o in opps} == {NoticeType.GRANT, NoticeType.FORECAST}
    posted = next(o for o in opps if o.external_id == "332894")
    assert posted.notice_type is NoticeType.GRANT
    assert posted.source_id == "grants_gov"
    assert posted.source_url == "https://www.grants.gov/search-results-detail/332894"
    assert posted.solicitation_number == "W911NF21S0009"
    assert posted.title == "LPS Qubit Collaboratory (LQC)"
    assert posted.buyer_org == "Dept of the Army -- Materiel Command"
    assert posted.aln == ["12.431"]
    assert posted.posted_at == datetime(2021, 4, 16, 4, 0, tzinfo=UTC)
    assert posted.response_due_at == datetime(2027, 4, 30, 4, 0, tzinfo=UTC)
    assert posted.detail_status is DetailStatus.PENDING
    assert posted.extra["opp_status"] == "posted" and posted.extra["agency_code"] == "DOD-AMC"
    forecast = next(o for o in opps if o.external_id == "334905")
    assert forecast.notice_type is NoticeType.FORECAST
    assert forecast.response_due_at is None  # closeDate ''
    assert "&amp;" not in forecast.title and "Research & Engineering" in forecast.title
    assert forecast.aln == ["12.006"]


def test_fetch_opportunity_enriches_eligibility_values_dates_and_documents() -> None:
    hit = next(h for h in HITS if h["id"] == "332894")
    opp = normalize_grants(hit, DETAIL)
    assert opp.notice_type is NoticeType.GRANT
    assert opp.detail_status is DetailStatus.FULL
    assert opp.buyer_org == "Department of Defense"
    assert opp.buyer_sub_org == "Dept of the Army -- Materiel Command"
    assert opp.buyer_hierarchy == ["Department of Defense", "Dept of the Army -- Materiel Command"]
    assert opp.aln == ["12.431"]
    assert opp.estimated_value_min is None and opp.estimated_value_max is None  # 'none'
    assert opp.response_due_at == datetime(2027, 4, 30, 4, 0, tzinfo=UTC)
    assert opp.posted_at == datetime(2021, 4, 16, 4, 0, tzinfo=UTC)
    assert opp.archive_at == datetime(2027, 5, 30, 4, 0, tzinfo=UTC)
    assert opp.description_text is not None
    assert opp.description_text.startswith("The U.S. Army Research Office (ARO)")
    assert "<p>" not in opp.description_text
    elig = opp.eligibility
    assert len(elig["applicant_types"]) == 8 and "12" in elig["applicant_type_codes"]
    assert elig["funding_instruments"] == ["Cooperative Agreement", "Grant", "Procurement Contract"]
    assert elig["funding_categories"] == [
        "Science and Technology and other Research and Development"
    ]
    assert elig["cost_sharing"] is False
    assert [d.file_name for d in opp.documents] == [
        "LQC BAA Final W911NF21S0009.pdf",
        opp.documents[1].file_name,
    ]
    assert opp.documents[0].url.endswith("/att/download/306813")
    assert opp.documents[0].mime_type == "application/pdf" and opp.documents[0].size == 887949
    assert opp.contacts[0].name == "Grants Officer"
    assert opp.contacts[0].title == "Grants/Agreements Officer"
    assert opp.contacts[0].email == "usarmy.rtp.devcom-arl.mesg.qcbox@army.mil"
    assert opp.extra["revision"] == 5 and opp.extra["category"] == "Discretionary"
    assert opp.extra["links"][0]["url"].startswith("https://www.arl.army.mil/")
    assert opp.extra["mod_comments"].startswith("Amendment 3")
    assert opp.status is None
    assert opp.source_tz == "America/New_York"


def test_forecast_detail_maps_estimates_and_ceiling_floor() -> None:
    hit = next(h for h in HITS if h["id"] == "334905")
    opp = normalize_grants(hit, FORECAST)
    assert opp.notice_type is NoticeType.FORECAST
    assert opp.detail_status is DetailStatus.FULL
    assert opp.estimated_value_min == Decimal("250000")
    assert opp.estimated_value_max == Decimal("2500000")
    assert opp.response_due_at == datetime(2027, 1, 15, 5, 0, tzinfo=UTC)  # est. response, EST
    assert opp.extra["est_synopsis_posting_at"] == "2026-11-01T04:00:00+00:00"
    assert opp.extra["fiscal_year"] == 2027
    assert opp.eligibility["eligibility_text"].startswith("Institutions of higher education")
    assert opp.eligibility["estimated_funding"] == "10000000"
    assert opp.eligibility["number_of_awards"] == "4"
    assert opp.buyer_hierarchy == ["Department of Defense"]  # top == agency collapses
    assert opp.documents == []
    assert opp.description_text is not None and "planning purposes" in opp.description_text
    assert is_forecast(hit, FORECAST) and not is_forecast(HITS[0], DETAIL)


def test_detail_only_and_closed_status_and_errors() -> None:
    opp = normalize_grants(None, DETAIL)
    assert opp.external_id == "332894" and opp.title == "LPS Qubit Collaboratory (LQC)"
    closed = normalize_grants({**HITS[0], "oppStatus": "closed"})
    assert closed.status is OpportunityStatus.CLOSED
    with pytest.raises(ValueError):
        normalize_grants({"title": "no id"})
