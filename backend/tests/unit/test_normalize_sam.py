"""M2-04: pure SAM.gov mapping (type codes, dates, hierarchy, amendment linking)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.normalize.sam import (
    NOTICE_TYPE_BY_CODE,
    link_amendments,
    map_contacts,
    map_place_of_performance,
    normalize_sam_notice,
    normalized_solicitation,
    notice_type_for,
    parse_sam_datetime,
    sam_date,
    sam_window_params,
    split_hierarchy,
    type_code,
)
from app.core.opportunity import DetailStatus, NoticeType, OpportunityStatus

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "sam_opps"


def _records() -> list[dict]:  # type: ignore[type-arg]
    page1 = json.loads((FIXTURES / "page1.json").read_text())
    page2 = json.loads((FIXTURES / "page2.json").read_text())
    return [*page1["opportunitiesData"], *page2["opportunitiesData"]]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("p", NoticeType.PRESOLICITATION),
        ("o", NoticeType.RFP),
        ("k", NoticeType.COMBINED),
        ("r", NoticeType.SOURCES_SOUGHT),
        ("s", NoticeType.SPECIAL),
        ("i", NoticeType.SPECIAL),
        ("a", NoticeType.AWARD),
        ("u", NoticeType.SPECIAL),
        ("g", NoticeType.SPECIAL),
    ],
)
def test_type_code_mapping(code: str, expected: NoticeType) -> None:
    assert NOTICE_TYPE_BY_CODE[code] is expected
    assert notice_type_for(code) is expected
    assert notice_type_for(code.upper()) is expected


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Presolicitation", "p"),
        ("Solicitation", "o"),
        ("Combined Synopsis/Solicitation", "k"),
        ("Sources Sought", "r"),
        ("Special Notice", "s"),
        ("Intent to Bundle Requirements (DoD-Funded)", "i"),
        ("Award Notice", "a"),
        ("Justification", "u"),
        ("Sale of Surplus Property", "g"),
    ],
)
def test_display_names_map_to_codes(name: str, code: str) -> None:
    assert type_code(name) == code
    assert notice_type_for(name) is NOTICE_TYPE_BY_CODE[code]


def test_unknown_types_fall_back_to_special() -> None:
    assert type_code(None) is None and type_code("") is None and type_code("zzz") is None
    assert type_code("x") is None
    assert notice_type_for("Brand New Type") is NoticeType.SPECIAL


def test_parse_sam_datetime_formats() -> None:
    assert parse_sam_datetime("2026-09-25T13:00:00-04:00") == datetime(
        2026, 9, 25, 17, 0, tzinfo=UTC
    )
    assert parse_sam_datetime("2026-09-25T13:00:00Z") == datetime(2026, 9, 25, 13, 0, tzinfo=UTC)
    # naive values are Eastern time (EDT in September)
    assert parse_sam_datetime("2026-09-25") == datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
    assert parse_sam_datetime("2026-09-25 13:00:00") == datetime(2026, 9, 25, 17, 0, tzinfo=UTC)
    assert parse_sam_datetime("01/15/2026") == datetime(2026, 1, 15, 5, 0, tzinfo=UTC)
    assert parse_sam_datetime(None) is None
    assert parse_sam_datetime("  ") is None
    assert parse_sam_datetime("not a date") is None
    assert parse_sam_datetime("2026-13-45") is None


def test_sam_date_and_window_params() -> None:
    start = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)  # still Aug 31 in Eastern time
    end = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    assert sam_date(start) == "08/31/2026"
    assert sam_window_params(start, end) == {"postedFrom": "08/31/2026", "postedTo": "09/26/2026"}
    with pytest.raises(ValueError):
        sam_window_params(end - timedelta(days=366), end)


def test_split_hierarchy() -> None:
    assert split_hierarchy("DEPT OF DEFENSE.DEPT OF THE ARMY.AMC.ACC.ACC-APG") == [
        "DEPT OF DEFENSE",
        "DEPT OF THE ARMY",
        "AMC",
        "ACC",
        "ACC-APG",
    ]
    assert split_hierarchy(None) == [] and split_hierarchy("  ") == []


def test_place_of_performance_and_contacts() -> None:
    pop = map_place_of_performance(
        {
            "streetAddress": "517 E Wisconsin Ave",
            "city": {"code": "53000", "name": "Milwaukee"},
            "state": {"code": "WI"},
            "zip": "53202",
            "country": {"code": "USA"},
        }
    )
    assert pop is not None
    assert (pop.city, pop.state, pop.country, pop.postal_code) == ("Milwaukee", "WI", "US", "53202")
    assert pop.raw == "517 E Wisconsin Ave" and pop.remote is False
    assert map_place_of_performance(None) is None
    contacts = map_contacts(
        [
            {
                "type": "primary",
                "email": "a@x.gov",
                "fullName": "A",
                "phone": None,
                "title": " CO ",
            },
            {"type": "secondary", "email": None, "fullName": None, "phone": None},
        ]
    )
    assert len(contacts) == 1
    assert contacts[0].model_dump() == {
        "name": "A",
        "title": "CO",
        "email": "a@x.gov",
        "phone": None,
        "kind": "primary",
    }
    assert map_contacts(None) == []


def test_fixture_records_normalize_to_opportunity_in() -> None:
    records = _records()
    opps = [normalize_sam_notice(r) for r in records]
    assert [o.notice_type for o in opps] == [
        NoticeType.PRESOLICITATION,
        NoticeType.RFP,
        NoticeType.COMBINED,
        NoticeType.SOURCES_SOUGHT,
        NoticeType.AWARD,
        NoticeType.SPECIAL,
    ]
    first = opps[0]
    assert first.source_id == "sam_opps"
    assert first.external_id == "3f9c1a2b7e8d4c5a9b0e1f2a3b4c5d6e"
    assert first.source_url == "https://sam.gov/opp/3f9c1a2b7e8d4c5a9b0e1f2a3b4c5d6e/view"
    assert first.region.value == "us" and first.country == "US" and first.currency == "USD"
    assert first.solicitation_number == "W911NF-26-R-0007"
    assert first.buyer_org == "DEPT OF DEFENSE"
    assert first.buyer_sub_org == "DEPT OF THE ARMY"
    assert first.buyer_office == "ACC-APG RTP DIV"
    assert first.buyer_hierarchy[-2:] == ["ACC-APG", "ACC-APG RTP DIV"]
    assert first.naics == ["541512"] and first.psc == ["DA01"]
    assert first.set_aside == "SBA"
    assert first.extra["set_aside_description"].startswith("Total Small Business")
    assert first.posted_at == datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    assert first.response_due_at == datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    assert first.archive_at == datetime(2026, 11, 4, 5, 0, tzinfo=UTC)  # EST after Nov 1
    assert first.source_tz == "America/New_York"
    assert [c.kind for c in first.contacts] == ["primary", "secondary"]
    assert first.place_of_performance is not None
    assert first.place_of_performance.state == "NC"
    assert [d.url for d in first.documents] == records[0]["resourceLinks"]
    # SAM download URLs carry no real name: the file id is the fallback until a HEAD probe
    assert first.documents[0].file_name == "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
    assert first.status is None and first.detail_status is DetailStatus.PENDING
    assert first.extra["type_code"] == "p" and first.extra["description_url"].startswith("https://")
    # amendment: merged naicsCodes, base type kept
    assert opps[1].naics == ["541512", "541519"]
    assert opps[1].extra["base_type_code"] == "p"
    # award notice: awarded status, award block kept, solicitation number trimmed
    assert opps[4].status is OpportunityStatus.AWARDED
    assert opps[4].solicitation_number == "47PF0018R0023"
    assert opps[4].extra["award"]["awardee"]["ueiSAM"] == "025114695AST"
    # sparse record: 'null' strings dropped, no contacts, no place of performance
    assert opps[5].contacts == [] and opps[5].place_of_performance is None
    assert "description_url" not in opps[5].extra
    assert opps[5].response_due_at is None
    # sources sought posted with a time component
    assert opps[3].posted_at == datetime(2026, 9, 23, 13, 15, tzinfo=UTC)
    assert opps[3].set_aside == "SDVOSBC"


def test_normalize_with_description_marks_detail_full() -> None:
    opp = normalize_sam_notice(_records()[0], description_text="<p>Scope</p>")
    assert opp.description_text == "<p>Scope</p>"
    assert opp.detail_status is DetailStatus.FULL


def test_normalize_requires_notice_id_and_falls_back_on_title() -> None:
    with pytest.raises(ValueError):
        normalize_sam_notice({"title": "x"})
    opp = normalize_sam_notice({"noticeId": "abc", "title": None, "department": "GSA"})
    assert opp.title == "SAM.gov notice abc"
    assert opp.buyer_org == "GSA" and opp.buyer_hierarchy == ["GSA"]
    assert opp.notice_type is NoticeType.SPECIAL


def test_link_amendments_points_later_notices_at_the_earliest() -> None:
    opps = link_amendments(normalize_sam_notice(r) for r in _records())
    by_id = {o.external_id: o for o in opps}
    parent = by_id["3f9c1a2b7e8d4c5a9b0e1f2a3b4c5d6e"]
    amendment = by_id["4a0d2b3c8f9e5d6b0c1f2a3b4c5d6e7f"]
    assert parent.parent_external_id is None
    assert amendment.parent_external_id == parent.external_id
    assert all(
        o.parent_external_id is None for o in opps if o.external_id not in {amendment.external_id}
    )
    # order-independent
    reversed_opps = link_amendments(reversed([normalize_sam_notice(r) for r in _records()]))
    assert {o.external_id: o.parent_external_id for o in reversed_opps} == {
        o.external_id: o.parent_external_id for o in opps
    }


def test_normalized_solicitation() -> None:
    assert normalized_solicitation(" 47PF0018R0023 ") == "47PF0018R0023"
    assert normalized_solicitation("w911nf-26-r-0007") == "W911NF26R0007"
    assert normalized_solicitation(None) is None and normalized_solicitation("--") is None
