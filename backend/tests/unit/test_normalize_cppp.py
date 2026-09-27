"""M3-02: CPPP listing row -> OpportunityIn mapping (pure)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Region
from app.core.normalize.cppp import (
    LATEST_ACTIVE_URL,
    SEARCH_URL,
    external_id_for,
    normalize_cppp,
    row_to_payload,
)
from app.core.normalize.nicgep import parse_listing_table
from app.core.opportunity import DetailStatus, NoticeType

CPPP = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "cppp"


def _payloads(name: str, page: str = "latest_active") -> list[dict[str, Any]]:
    return [
        row_to_payload(r, page=page) for r in parse_listing_table((CPPP / name).read_text()).rows
    ]


def test_latest_active_row_maps_to_the_canonical_schema() -> None:
    payload = _payloads("latest_active.html")[0]
    opp = normalize_cppp(payload)
    assert opp.source_id == "cppp" and opp.external_id == "14156660"
    assert opp.region is Region.IN and opp.country == "IN" and opp.currency == "INR"
    assert opp.notice_type is NoticeType.RFP
    assert opp.title == "BRANDING AND FABRICATION"
    assert opp.solicitation_number == "2600027962-HD-07350"
    assert opp.buyer_org == "Hindustan Petroleum Corporation Limited"
    assert opp.buyer_hierarchy == ["Hindustan Petroleum Corporation Limited"]
    assert opp.buyer_sub_org is None and opp.buyer_office is None
    assert opp.posted_at == datetime(2026, 9, 27, 4, 30, tzinfo=UTC)  # 10:00 IST
    assert opp.response_due_at == datetime(2026, 10, 19, 9, 30, tzinfo=UTC)  # 3 PM IST
    assert opp.opening_at == datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
    assert opp.source_tz == "Asia/Kolkata"
    assert opp.source_url is not None and opp.source_url.startswith(
        "https://eprocure.gov.in/cppp/tendersfullview/"
    )
    # detail needs a CAPTCHA: manual + portal search URL (SPEC 5.1)
    assert opp.detail_status is DetailStatus.MANUAL
    assert (
        opp.extra["manual_detail_url"] == SEARCH_URL == "https://eprocure.gov.in/cppp/tendersearch"
    )
    assert opp.extra["tender_id"] == "137089" and opp.extra["cppp_internal_id"] == "14156660"
    assert opp.extra["detail_link_expires_at"].startswith("2026-09-")
    assert opp.extra["listing_page"] == "latest_active" and opp.extra["corrigendum"] is False
    assert opp.documents == []


def test_organisation_chain_and_corrigendum_flag() -> None:
    payloads = _payloads("org_listing.html", page="by_organisation")
    corr = normalize_cppp(payloads[1])
    assert corr.notice_type is NoticeType.CORRIGENDUM and corr.extra["corrigendum"] is True
    assert corr.buyer_org == "Ministry of Petroleum and Natural Gas"
    assert corr.buyer_sub_org == "Hindustan Petroleum Corporation Limited"
    assert corr.buyer_office == "Visakh Refinery"
    assert corr.buyer_hierarchy == [
        "Ministry of Petroleum and Natural Gas",
        "Hindustan Petroleum Corporation Limited",
        "Visakh Refinery",
    ]
    assert corr.extra["tender_id"] == "2026_HPCL_138011_1"
    assert corr.external_id == "14157001"


def test_external_id_fallbacks() -> None:
    assert external_id_for({"detail_url": None, "reference": "NIT/12/2026", "tender_id": "77"}) == (
        "NIT-12-2026-77"
    )
    hashed = external_id_for({"title": "A", "organisation": "B", "closing": "C"})
    assert hashed.startswith("h-") and len(hashed) == 22
    assert hashed == external_id_for({"title": "A", "organisation": "B", "closing": "C"})


def test_unparseable_dates_become_none_and_missing_title_raises() -> None:
    payloads = _payloads("malformed.html")
    bad = normalize_cppp(payloads[1])
    assert bad.title == "SUPPLY OF SPARES"
    assert bad.posted_at is None and bad.response_due_at is None and bad.opening_at is None
    assert bad.source_url != LATEST_ACTIVE_URL
    with pytest.raises(ValueError, match="no title"):
        normalize_cppp({"title": " ", "page": "latest_active"})


def test_row_without_detail_link_points_at_the_listing_page() -> None:
    opp = normalize_cppp({"title": "Road works", "reference": "R/1", "tender_id": "9"})
    assert opp.source_url == LATEST_ACTIVE_URL and opp.external_id == "R-1-9"
    assert "detail_link_expires_at" not in opp.extra
