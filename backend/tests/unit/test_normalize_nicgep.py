"""M3-02/M3-05: HTML table extractor and the NIC GePNIC-family listing parsers (pure)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.core.normalize.html_tables import extract_tables, find_table, tables_with_header
from app.core.normalize.nicgep import (
    decode_cppp_token,
    parse_home_latest,
    parse_listing_table,
    parse_org_index,
    parse_portal_datetime,
    split_organisation_chain,
)

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures"
CPPP = FIXTURES / "cppp"
LATEST = (CPPP / "latest_active.html").read_text()
ORG_LISTING = (CPPP / "org_listing.html").read_text()
BYORG = (CPPP / "byorg.html").read_text()
HPCL_TOKEN = (
    "https://eprocure.gov.in/cppp/tendersfullview/MTQxNTY2NjA=A13h1OGQ2NzAxYTMwZTJhNTIxMGNiNmEw"
    "M2EzNmNhYWZhODk=A13h1OGQ2NzAxYTMwZTJhNTIxMGNiNmEwM2EzNmNhYWZhODk=A13h1MTc5MDQ4NTEwMg==A13h1"
    "MjYwMDAyNzk2Mi1IRC0wNzM1MA==A13h1MTM3MDg5"
)


# --- html_tables -------------------------------------------------------------------------


def test_extract_tables_handles_nesting_links_and_line_breaks() -> None:
    html = """
    <table id="outer"><tr><td>wrapper
      <table id="inner" class="list_table x">
        <tr><th>Name</th><th>Link</th></tr>
        <tr><td>first<br/>second &amp; third</td><td><a href="/a?x=1&amp;y=2">Go</a></td></tr>
        <tr><td>&nbsp;</td></tr>
      </table>
    </td></tr></table>
    <script>var t = "<table><tr><td>ignored</td></tr></table>";</script>
    """
    tables = extract_tables(html)
    assert [t.id for t in tables] == ["inner", "outer"]
    inner = find_table(html, table_id="inner")
    assert inner is not None and inner.classes == {"list_table", "x"}
    assert inner.header == ["Name", "Link"]
    body = inner.body
    assert len(body) == 2
    assert body[0].cells[0].text == "first second & third"
    assert body[0].cells[0].lines == ["first", "second & third"]
    assert body[0].cells[1].links[0].href == "/a?x=1&y=2"
    assert body[0].cells[1].links[0].text == "Go"
    assert body[1].texts == [""]
    outer = find_table(html, table_id="outer")
    assert outer is not None and outer.rows[0].cells[0].text == "wrapper"
    assert tables_with_header(html, "name", "link")[0].id == "inner"
    assert extract_tables(None) == [] and extract_tables("") == []
    assert find_table("<p>no tables</p>", css_class="list_table") is None


def test_extract_tables_tolerates_unclosed_markup() -> None:
    html = "<table id='t'><tr><td>a<td>b<tr><td>c"
    table = find_table(html, table_id="t")
    assert table is not None
    assert [r.texts for r in table.rows] == [["a", "b"], ["c"]]


# --- dates -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected", "has_time"),
    [
        ("27-Sep-2026 10:00 AM", datetime(2026, 9, 27, 4, 30, tzinfo=UTC), True),
        ("19-Oct-2026 03:00 PM", datetime(2026, 10, 19, 9, 30, tzinfo=UTC), True),
        ("19-Oct-2026", datetime(2026, 10, 18, 18, 30, tzinfo=UTC), False),
        ("17/07/2026 08:23 PM", datetime(2026, 7, 17, 14, 53, tzinfo=UTC), True),  # parse_in
        ("  14-Oct-2026\xa003:00 PM ", datetime(2026, 10, 14, 9, 30, tzinfo=UTC), True),
    ],
)
def test_parse_portal_datetime(text: str, expected: datetime, has_time: bool) -> None:
    parsed = parse_portal_datetime(text)
    assert parsed is not None
    assert parsed.utc == expected and parsed.has_time is has_time
    assert parsed.source_tz == "Asia/Kolkata"


@pytest.mark.parametrize("text", [None, "", "--", "NA", "soon", "32-Jan-2026"])
def test_parse_portal_datetime_bad_input_is_none(text: str | None) -> None:
    assert parse_portal_datetime(text) is None


def test_parse_portal_datetime_uses_declared_formats_first() -> None:
    parsed = parse_portal_datetime("2026/10/19 15:00", formats=("%Y/%m/%d %H:%M",))
    assert parsed is not None and parsed.utc == datetime(2026, 10, 19, 9, 30, tzinfo=UTC)


# --- organisation chain --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("Hindustan Petroleum Corporation Limited", ["Hindustan Petroleum Corporation Limited"]),
        (
            "Bharat Heavy Electricals Limited, Hyderabad",
            ["Bharat Heavy Electricals Limited, Hyderabad"],
        ),
        (
            "Ministry of Petroleum and Natural Gas||Hindustan Petroleum Corporation Limited||"
            "Marketing Division",
            [
                "Ministry of Petroleum and Natural Gas",
                "Hindustan Petroleum Corporation Limited",
                "Marketing Division",
            ],
        ),
        ("Govt of Tamil Nadu | PWD > Buildings", ["Govt of Tamil Nadu", "PWD", "Buildings"]),
    ],
)
def test_split_organisation_chain(value: str | None, expected: list[str]) -> None:
    assert split_organisation_chain(value) == expected


# --- listing table -------------------------------------------------------------------------


def test_parse_listing_table_reads_the_real_cppp_capture() -> None:
    outcome = parse_listing_table(LATEST)
    assert outcome.found and outcome.notes == []
    assert len(outcome.rows) == 6
    first = outcome.rows[0]
    assert first.title == "BRANDING AND FABRICATION"
    assert first.reference == "2600027962-HD-07350" and first.tender_id == "137089"
    assert first.organisation == "Hindustan Petroleum Corporation Limited"
    assert first.published == "27-Sep-2026 10:00 AM"
    assert first.closing == "19-Oct-2026 03:00 PM" and first.opening == "19-Oct-2026 03:00 PM"
    assert first.detail_url == HPCL_TOKEN
    assert first.corrigendum is False
    # a reference that itself contains slashes keeps them; the last segment is the code
    with_slashes = next(r for r in outcome.rows if "EE-II" in (r.reference or ""))
    assert with_slashes.reference == "158/EE-II/SD/AE-HPU/2026-27"
    assert with_slashes.tender_id == "170337"
    bpcl = next(r for r in outcome.rows if r.tender_id == "2026_BPCL_26796")
    assert bpcl.reference == "1000465730"


def test_parse_listing_table_reads_the_organisation_chain_and_corrigendum() -> None:
    outcome = parse_listing_table(ORG_LISTING)
    assert outcome.found and len(outcome.rows) == 3
    second = outcome.rows[1]
    assert second.tender_id == "2026_HPCL_138011_1" and second.corrigendum is True
    assert second.organisation_chain == [
        "Ministry of Petroleum and Natural Gas",
        "Hindustan Petroleum Corporation Limited",
        "Visakh Refinery",
    ]
    assert outcome.rows[0].corrigendum is False


def test_parse_listing_table_notes_missing_table_and_bad_rows() -> None:
    missing = parse_listing_table("<html><table id='x'><tr><th>Foo</th></tr></table></html>")
    assert not missing.found and missing.rows == []
    assert "layout change" in missing.notes[0] and "Title" in missing.notes[0]
    malformed = parse_listing_table((CPPP / "malformed.html").read_text())
    assert malformed.found
    titles = [r.title for r in malformed.rows]
    assert titles == ["BRANDING AND FABRICATION", "SUPPLY OF SPARES"]
    assert any("skipped" in n for n in malformed.notes)


# --- home marquee and organisation index -------------------------------------------------


def test_parse_home_latest_from_the_real_gepnic_front_page() -> None:
    html = (FIXTURES / "gepnic_tn" / "home.html").read_text()
    outcome = parse_home_latest(html)
    assert outcome.found and outcome.notes == []
    assert len(outcome.rows) >= 3
    first = outcome.rows[0]
    assert first.title == "Formation of park at KRG Nagar in Ward No 20 North Zone"
    assert first.reference == "e71/2026-NZ"
    assert first.closing == "14-Oct-2026 03:00 PM" and first.opening == "15-Oct-2026 04:00 PM"
    assert first.detail_url is not None and "DirectLink" in first.detail_url
    assert first.organisation is None and first.tender_id is None
    assert not parse_home_latest("<html></html>").found


def test_parse_org_index() -> None:
    outcome = parse_org_index(BYORG)
    assert outcome.found and [o.name for o in outcome.rows][:2] == [
        "Hindustan Petroleum Corporation Limited",
        "Bharat Heavy Electricals Limited",
    ]
    assert outcome.rows[0].count == 2
    assert outcome.rows[0].url == "https://eprocure.gov.in/cppp/tendersbyorganisation/HPCL"
    assert outcome.rows[3].count == 0
    assert not parse_org_index(LATEST).found


# --- CPPP token ----------------------------------------------------------------------------


def test_decode_cppp_token() -> None:
    token = decode_cppp_token(HPCL_TOKEN)
    assert token == {
        "internal_id": "14156660",
        "expires": "1790485102",
        "reference": "2600027962-HD-07350",
        "code": "137089",
    }
    assert decode_cppp_token("https://eprocure.gov.in/cppp/latestactivetendersnew") == {}
    assert decode_cppp_token(None) == {}
    assert decode_cppp_token("https://eprocure.gov.in/cppp/tendersfullview/@@notbase64") == {}
