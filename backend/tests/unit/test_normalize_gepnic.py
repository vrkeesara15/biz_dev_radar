"""M3-05: GePNIC portal config + row -> OpportunityIn mapping (pure)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Region
from app.core.normalize.gepnic import (
    PAGE_HOME,
    PAGE_ORG,
    PortalConfig,
    dedupe_key,
    external_id_for,
    normalize_gepnic,
    row_to_payload,
)
from app.core.normalize.nicgep import parse_home_latest, parse_listing_table
from app.core.opportunity import DetailStatus, NoticeType

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"
TN = PortalConfig(
    source_id="gepnic_tn",
    display_name="Tamil Nadu Tenders (tntenders.gov.in)",
    state="Tamil Nadu",
    portal_home="https://tntenders.gov.in/",
    base_url="https://tntenders.gov.in/nicgep/app",
)


def _home_payloads() -> list[dict[str, Any]]:
    html = (FIXTURES / "gepnic_tn" / "home.html").read_text()
    return [row_to_payload(r, page=PAGE_HOME) for r in parse_home_latest(html).rows]


def _org_payloads() -> list[dict[str, Any]]:
    html = (FIXTURES / "gepnic_tn" / "org_listing.html").read_text()
    rows = parse_listing_table(html).rows
    return [row_to_payload(r, page=PAGE_ORG, organisation="Fallback Org") for r in rows]


def test_portal_config_from_dict_defaults_and_validation() -> None:
    cfg = PortalConfig.from_dict(
        {
            "source_id": "gepnic_xx",
            "display_name": "X",
            "state": "X",
            "portal_home": "https://x.gov.in/",
            "base_url": "https://x.gov.in/nicgep/app",
            "date_formats": ["%d/%m/%Y %H:%M"],
            "enabled": False,
            "health_status": "robots_disallowed",
            "unknown_key": 1,
        }
    )
    assert cfg.date_formats == ("%d/%m/%Y %H:%M",) and cfg.tz == "Asia/Kolkata"
    assert cfg.enabled is False and cfg.health_status == "robots_disallowed"
    assert cfg.extra == {"unknown_key": 1}
    assert cfg.home_url == "https://x.gov.in/nicgep/app"
    assert cfg.org_list_url.endswith("?page=FrontEndTendersByOrganisation&service=page")
    assert cfg.search_url.endswith("?page=FrontEndAdvancedSearch&service=page")
    assert cfg.absolute("/nicgep/app?x=1") == "https://x.gov.in/nicgep/app?x=1"
    assert cfg.absolute(None) is None
    with pytest.raises(ValueError, match="base_url"):
        PortalConfig.from_dict(
            {"source_id": "a", "display_name": "b", "state": "c", "portal_home": "d"}
        )


def test_front_page_row_maps_with_search_url_fallback_and_manual_detail() -> None:
    payload = _home_payloads()[0]
    opp = normalize_gepnic(payload, TN)
    assert opp.source_id == "gepnic_tn" and opp.region is Region.IN and opp.currency == "INR"
    assert opp.title == "Formation of park at KRG Nagar in Ward No 20 North Zone"
    assert opp.solicitation_number == "e71/2026-NZ"
    assert opp.response_due_at == datetime(2026, 10, 14, 9, 30, tzinfo=UTC)  # 3 PM IST
    assert opp.opening_at == datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
    assert opp.posted_at is None and opp.buyer_org is None
    assert opp.source_tz == "Asia/Kolkata"
    # session-bound DirectLink made absolute; search page kept as the fallback
    assert opp.source_url is not None
    assert opp.source_url.startswith("https://tntenders.gov.in/nicgep/app?component=%24DirectLink")
    assert opp.extra["portal_search_url"] == TN.search_url
    assert opp.extra["link_is_session_bound"] is True
    assert opp.detail_status is DetailStatus.MANUAL
    assert opp.extra["portal"] == "gepnic_tn" and opp.extra["state"] == "Tamil Nadu"
    assert opp.extra["listing_page"] == PAGE_HOME and opp.extra["tender_id"] is None
    # no tender id on the front page: reference + title hash, stable
    assert (
        opp.external_id.startswith("e71-2026-NZ-")
        and len(opp.external_id) == len("e71-2026-NZ-") + 12
    )
    assert external_id_for(payload) == opp.external_id


def test_organisation_listing_row_has_tender_id_chain_and_corrigendum() -> None:
    payloads = _org_payloads()
    first = normalize_gepnic(payloads[0], TN)
    assert (
        first.external_id == "2026_TNCMC_871234_1" and first.extra["tender_id"] == first.external_id
    )
    assert first.buyer_org == "Government of Tamil Nadu"
    assert first.buyer_sub_org == "Municipal Administration and Water Supply"
    assert first.buyer_office == "Coimbatore City Municipal Corporation"
    assert first.posted_at == datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    assert first.notice_type is NoticeType.RFP
    second = normalize_gepnic(payloads[1], TN)
    assert second.notice_type is NoticeType.CORRIGENDUM and second.extra["corrigendum"] is True
    assert second.solicitation_number == "TANGEDCO/2026/CE/Chennai/117"
    assert second.buyer_hierarchy[-1] == "Chennai Region"


def test_row_without_link_uses_the_search_page_and_org_fallback() -> None:
    opp = normalize_gepnic(
        {
            "title": "Road works",
            "reference": "R/1",
            "organisation": "Fallback Org",
            "page": PAGE_ORG,
        },
        TN,
    )
    assert opp.source_url == TN.search_url and opp.extra["link_is_session_bound"] is False
    assert opp.buyer_org == "Fallback Org"
    with pytest.raises(ValueError, match="no title"):
        normalize_gepnic({"title": ""}, TN)


def test_dedupe_key_matches_front_page_and_listing_forms_of_the_same_tender() -> None:
    home = _home_payloads()[0]
    listed = _org_payloads()[0]
    # the marquee row carries no tender id, so reference + title is the shared key
    assert dedupe_key(listed) == dedupe_key(home)
    assert dedupe_key({**listed, "tender_id": None}) == dedupe_key(home)
    assert dedupe_key(listed) != dedupe_key(_org_payloads()[1])
    assert dedupe_key({"title": "A", "reference": None}) == "rt:|a"
    # only a row with neither reference nor title falls back to the tender id
    assert dedupe_key({"tender_id": "2026_TNCMC_871234_1"}) == "id:2026_TNCMC_871234_1"


def test_date_formats_from_config_are_tried_first() -> None:
    cfg = PortalConfig(
        source_id="gepnic_x",
        display_name="X",
        state="X",
        portal_home="https://x/",
        base_url="https://x/app",
        date_formats=("%Y/%m/%d %H:%M",),
    )
    opp = normalize_gepnic({"title": "t", "closing": "2026/10/19 15:00"}, cfg)
    assert opp.response_due_at == datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
    fallback = normalize_gepnic({"title": "t", "closing": "19-Oct-2026 03:00 PM"}, cfg)
    assert fallback.response_due_at == datetime(2026, 10, 19, 9, 30, tzinfo=UTC)
