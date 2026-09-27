"""M3-03: GeM listing document -> OpportunityIn mapping (pure)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Region
from app.core.normalize.gem import (
    ALIASES,
    bid_number,
    document_url,
    field,
    is_reverse_auction,
    normalize_gem,
    parse_gem_datetime,
)
from app.core.opportunity import DetailStatus, NoticeType

GEM = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "gem"
PAGE1 = json.loads((GEM / "all_bids_page1.json").read_text())
DOCS: list[dict[str, Any]] = PAGE1["response"]["response"]["docs"]
BID_PAGE = "https://bidplus.gem.gov.in/showbidDocument/{bid_id}"
SELLER = "https://gem.gov.in/register/seller/signup"


def _norm(doc: dict[str, Any]) -> Any:
    return normalize_gem(doc, bid_page_url=BID_PAGE, seller_registration_url=SELLER)


def test_bid_maps_to_gem_bid_with_ministry_department_and_end_date_ist() -> None:
    opp = _norm(DOCS[0])
    assert opp.source_id == "gem" and opp.external_id == "GEM/2026/B/1234567"
    assert opp.notice_type is NoticeType.GEM_BID
    assert opp.region is Region.IN and opp.country == "IN" and opp.currency == "INR"
    assert opp.title == "Desktop Computers x 120 (GEM/2026/B/1234567)"
    assert opp.solicitation_number == "GEM/2026/B/1234567"
    assert opp.buyer_org == "Ministry of Railways"
    assert opp.buyer_sub_org == "South Central Railway"
    assert opp.buyer_office == "Divisional Railway Manager Office Secunderabad"
    assert opp.buyer_hierarchy[0] == "Ministry of Railways"
    assert opp.india_category == ["Desktop Computers"]
    # 17-Oct-2026 08:23 PM IST == 14:53 UTC, source zone kept as IST
    assert opp.response_due_at == datetime(2026, 10, 17, 14, 53, tzinfo=UTC)
    assert opp.posted_at == datetime(2026, 9, 22, 14, 53, tzinfo=UTC)
    assert opp.source_tz == "Asia/Kolkata"
    assert opp.source_url == "https://bidplus.gem.gov.in/showbidDocument/7891234"
    assert [d.url for d in opp.documents] == ["https://bidplus.gem.gov.in/showbidDocument/7891234"]
    assert opp.documents[0].file_name == "GEM-2026-B-1234567.pdf"
    assert opp.documents[0].mime_type == "application/pdf"
    assert opp.eligibility == {"requires_gem_registration": True}
    assert opp.extra["gem_seller_registration_url"] == SELLER
    assert opp.extra["gem_bid_id"] == "7891234" and opp.extra["quantity"] == 120
    assert opp.extra["bid_type"] == "Bid" and opp.extra["bid_end_display"] == "17-Oct-2026 08:23 PM"
    assert opp.detail_status is DetailStatus.PENDING


def test_reverse_auction_bid_type() -> None:
    ra = next(d for d in DOCS if d["b_bid_type"] == ["RA"])
    opp = _norm(ra)
    assert opp.notice_type is NoticeType.REVERSE_AUCTION and opp.extra["bid_type"] == "RA"
    assert is_reverse_auction({"bid_type": "Reverse Auction"})
    assert is_reverse_auction({"b_is_ra": ["true"]}) and is_reverse_auction({"is_ra": 1})
    assert not is_reverse_auction({"b_bid_type": ["Bid"]}) and not is_reverse_auction({})


def test_aliases_and_unwrapping() -> None:
    doc = {"bid_number": "GEM/2026/B/1", "quantity": "1,200", "ministry": ["Ministry of X"]}
    assert bid_number(doc) == "GEM/2026/B/1"
    assert field(doc, "ministry") == "Ministry of X"
    opp = _norm({**doc, "bid_id": "42", "bid_end_date": "17-Jul-2026 08:23 PM"})
    assert opp.extra["quantity"] == 1200 and opp.title.startswith("GeM bid x 1200")
    assert opp.response_due_at == datetime(2026, 7, 17, 14, 53, tzinfo=UTC)
    assert document_url({"pdf_url": "https://x/y.pdf"}, BID_PAGE) == "https://x/y.pdf"
    assert document_url({}, BID_PAGE) is None
    assert all(len(v) >= 2 for v in ALIASES.values())


def test_missing_bid_number_raises_and_missing_id_falls_back_to_the_portal_home() -> None:
    with pytest.raises(ValueError, match="bid number"):
        _norm({"b_category_name": ["x"]})
    opp = _norm({"b_bid_number": ["GEM/2026/B/9"], "bid_end_date_display": "17-Jul-2026 08:23 PM"})
    assert opp.source_url == "https://gem.gov.in/" and opp.documents == []
    assert opp.response_due_at == datetime(2026, 7, 17, 14, 53, tzinfo=UTC)  # display fallback
    assert opp.title == "GeM bid (GEM/2026/B/9)" and opp.buyer_org is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-17T14:53:00Z", datetime(2026, 7, 17, 14, 53, tzinfo=UTC)),
        ("17-Jul-2026 08:23 PM", datetime(2026, 7, 17, 14, 53, tzinfo=UTC)),
        ("17-07-2026 20:23:00", datetime(2026, 7, 17, 14, 53, tzinfo=UTC)),
        (1784645580, datetime(2026, 7, 21, 14, 53, tzinfo=UTC)),
        (1784645580000, datetime(2026, 7, 21, 14, 53, tzinfo=UTC)),
        (None, None),
        ("soon", None),
    ],
)
def test_parse_gem_datetime(value: Any, expected: datetime | None) -> None:
    assert parse_gem_datetime(value) == expected
