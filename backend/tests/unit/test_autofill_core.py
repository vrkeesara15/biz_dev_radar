"""M1-10: deterministic autofill mapping (LLM extraction -> suggestions, SAM entity ->
suggestions, region gating). Pure core, no I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.core.autofill import (
    SAM_CONFIDENCE,
    SAM_SELF_ASSERTED_CONFIDENCE,
    SAM_SOURCE_REF,
    AutofillExtraction,
    Suggestion,
    filter_for_region,
    is_valid_uei,
    map_sam_entity,
    suggestions_from_extraction,
)
from app.core.config import Region
from pydantic import ValidationError

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "adapters"
    / "fixtures"
    / "sam_entity"
    / "entity_ALPHA1234567.json"
)


def _by_field(suggestions: list[Suggestion]) -> dict[str, list[Suggestion]]:
    out: dict[str, list[Suggestion]] = {}
    for s in suggestions:
        out.setdefault(s.field, []).append(s)
    return out


def test_is_valid_uei() -> None:
    assert is_valid_uei("ALPHA1234567") and is_valid_uei(" alpha1234567 ")
    assert (
        not is_valid_uei("SHORT") and not is_valid_uei("ALPHA-1234567") and not is_valid_uei(None)
    )


def test_extraction_maps_to_endpoint_shaped_values_with_confidence_and_pages() -> None:
    extraction = AutofillExtraction(
        legal_name="  Alpha Federal Solutions, LLC ",
        dba_names=["Alpha Federal", ""],
        addresses=[
            {"line1": "1750 Tysons Blvd", "city": "McLean", "state": "VA", "country": "usa"},
            {"line1": "No country", "city": "Nowhere", "country": "Atlantis"},
        ],
        website="alphafederal.example",
        phone="+1 (703) 555-0100",
        year_founded=2009,
        legal_structure="llc",
        uei="alpha1234567",
        cage_code="7abc1",
        naics_codes=["541512", "541512", "999999", "541511"],
        psc_codes=["D302", "bad code"],
        service_lines=[
            {"name": "Cloud migration", "description": "word " * 200, "page": 2},
            {"name": "", "description": "dropped"},
        ],
        certifications=[{"kind": "cmmc", "level": "2", "evidence": "CMMC Level 2", "page": 3}],
        past_performance=[
            {
                "title": "IRS consolidation",
                "customer": "IRS",
                "scope": "Consolidated data centers",
                "role": "sub",
                "period_start": "2021",
                "period_end": "2023-06-30",
                "naics": "541512",
                "page": 4,
            },
            {"title": "no scope", "customer": "x", "scope": "  "},
        ],
        personnel=[{"name": "Pat Quinn", "role": "CEO", "page": 1}],
        company_overview="Alpha builds <secure> clouds.\n\nSince 2009.",
        field_confidence=[
            {"field": "legal_name", "confidence": 0.99, "page": 1},
            {"field": "phone", "confidence": 1.0},
            {"field": "naics_codes", "confidence": 0.9, "page": 2},
        ],
    )
    suggestions = suggestions_from_extraction(
        extraction,
        source="capability_pdf",
        source_ref=lambda page: f"file:abc#page={page}" if page else "file:abc",
    )
    by = _by_field(suggestions)
    assert set(by) == {
        "legal_name",
        "dba_names[]",
        "addresses[]",
        "website",
        "phone",
        "year_founded",
        "legal_structure",
        "uei",
        "cage_code",
        "codes.naics[]",
        "codes.psc[]",
        "service_lines[]",
        "certifications[]",
        "past_performance[]",
        "personnel[]",
        "boilerplate[]",
    }
    legal = by["legal_name"][0]
    assert legal.value == "Alpha Federal Solutions, LLC" and legal.confidence == 0.99
    assert legal.source == "capability_pdf" and legal.source_ref == "file:abc#page=1"
    assert by["dba_names[]"][0].value == "Alpha Federal" and len(by["dba_names[]"]) == 1
    assert by["addresses[]"][0].value == {
        "kind": "hq",
        "line1": "1750 Tysons Blvd",
        "line2": None,
        "city": "McLean",
        "state": "VA",
        "postal_code": None,
        "country": "US",
    }
    assert len(by["addresses[]"]) == 1  # the unknown country was dropped
    assert by["website"][0].value == "https://alphafederal.example"
    assert by["phone"][0].confidence == 1.0 and by["phone"][0].value == "+1 (703) 555-0100"
    assert by["year_founded"][0].value == 2009 and by["legal_structure"][0].value == "llc"
    assert by["uei"][0].value == "ALPHA1234567" and by["cage_code"][0].value == "7ABC1"
    naics = by["codes.naics[]"]
    assert [s.value for s in naics] == [
        {"code": "541512", "is_primary": True},
        {"code": "541511", "is_primary": False},
    ]
    assert all(s.confidence == 0.9 and s.source_ref == "file:abc#page=2" for s in naics)
    assert [s.value for s in by["codes.psc[]"]] == [{"code": "D302", "is_primary": False}]
    line = by["service_lines[]"][0]
    assert line.value["name"] == "Cloud migration" and len(line.value["description"].split()) == 150
    assert line.source_ref == "file:abc#page=2" and line.confidence == 0.7  # PDF default
    assert by["certifications[]"][0].value == {
        "kind": "cmmc",
        "level": "2",
        "notes": "CMMC Level 2",
    }
    assert by["certifications[]"][0].source_ref == "file:abc#page=3"
    pp = by["past_performance[]"]
    assert len(pp) == 1 and pp[0].value == {
        "title": "IRS consolidation",
        "customer": "IRS",
        "role": "sub",
        "scope": "Consolidated data centers",
        "period_start": "2021-01-01",
        "period_end": "2023-06-30",
        "naics": "541512",
    }
    assert by["personnel[]"][0].value == {"name": "Pat Quinn", "role": "CEO"}
    assert by["boilerplate[]"][0].value == {
        "kind": "company_overview",
        "title": "Company overview",
        "body": "<p>Alpha builds &lt;secure&gt; clouds.</p><p>Since 2009.</p>",
    }
    # website source: default 0.6 and the URL as source_ref for every field
    web = suggestions_from_extraction(
        AutofillExtraction(legal_name="X", year_founded=1700, phone="call us"),
        source="website",
        source_ref=lambda _p: "https://x.example",
    )
    assert [(s.field, s.confidence, s.source_ref) for s in web] == [
        ("legal_name", 0.6, "https://x.example")
    ]  # bad year and non-numeric phone dropped


def test_extraction_schema_rejects_unknown_certification_kinds_and_roles() -> None:
    with pytest.raises(ValidationError):
        AutofillExtraction(certifications=[{"kind": "iso_99999"}])
    with pytest.raises(ValidationError):
        AutofillExtraction(
            past_performance=[{"title": "t", "customer": "c", "scope": "s", "role": "x"}]
        )
    with pytest.raises(ValidationError):
        AutofillExtraction(field_confidence=[{"field": "legal_name", "confidence": 2}])
    assert AutofillExtraction().field_confidence == [] and AutofillExtraction().legal_name is None


def test_sam_entity_fixture_maps_deterministically() -> None:
    payload = json.loads(FIXTURE.read_text())
    suggestions, warnings = map_sam_entity(payload, uei="ALPHA1234567")
    assert warnings == []
    assert all(s.source == "uei" and s.source_ref == SAM_SOURCE_REF for s in suggestions)
    by = _by_field(suggestions)
    assert by["uei"][0].value == "ALPHA1234567"
    assert by["legal_name"][0].value == "ALPHA FEDERAL SOLUTIONS, LLC"
    assert by["legal_name"][0].confidence == SAM_CONFIDENCE
    assert by["dba_names[]"][0].value == "Alpha Federal"
    assert by["cage_code"][0].value == "7ABC1"
    assert by["sam_status"][0].value == "active"
    assert by["sam_expires_on"][0].value == "2027-03-01"
    assert by["website"][0].value == "https://www.alphafederal.example"
    assert by["year_founded"][0].value == 2009
    assert (
        by["legal_structure"][0].value == "corporation"
        and by["legal_structure"][0].confidence == 0.85
    )
    assert by["addresses[]"][0].value == {
        "kind": "hq",
        "line1": "1750 TYSONS BLVD",
        "line2": "STE 1500",
        "city": "MCLEAN",
        "state": "VA",
        "postal_code": "22102-4201",
        "country": "US",
    }
    assert [s.value for s in by["codes.naics[]"]] == [
        {"code": "541512", "is_primary": True},
        {"code": "541511", "is_primary": False},
    ]  # 999999 is not a NAICS code
    assert [s.value["code"] for s in by["codes.psc[]"]] == ["D302", "D399"]
    certs = {s.value["kind"]: s for s in by["certifications[]"]}
    assert set(certs) == {"8a", "hubzone", "vosb"}
    assert certs["8a"].value == {
        "kind": "8a",
        "issued_by": "SBA",
        "issued_on": "2022-09-01",
        "expires_on": "2031-08-31",
    }
    assert certs["hubzone"].value == {
        "kind": "hubzone",
        "issued_by": "SBA",
        "issued_on": "2023-02-14",
    }
    assert certs["8a"].confidence == SAM_CONFIDENCE
    assert certs["vosb"].confidence == SAM_SELF_ASSERTED_CONFIDENCE
    assert "self-asserted" in certs["vosb"].value["notes"]


def test_sam_entity_edge_cases() -> None:
    assert map_sam_entity({"totalRecords": 0, "entityData": []}, uei="ALPHA1234567") == (
        [],
        ["SAM.gov has no entity registration for UEI ALPHA1234567"],
    )
    assert map_sam_entity("garbage", uei="X")[0] == []
    payload = {
        "entityData": [
            {
                "entityRegistration": {
                    "ueiSAM": "OTHER1234567",
                    "legalBusinessName": "Other",
                    "registrationStatus": "Expired",
                    "registrationExpirationDate": "not-a-date",
                },
                "coreData": {
                    "generalInformation": {
                        "entityStructureCode": "2K",
                        "entityStructureDesc": "Partnership or Limited Liability Partnership",
                    },
                    "physicalAddress": {"addressLine1": "x", "city": "y", "countryCode": "ZZZ"},
                },
                "assertions": {"goodsAndServices": {"primaryNaics": "541330", "naicsList": []}},
            }
        ]
    }
    suggestions, warnings = map_sam_entity(payload, uei="ALPHA1234567")
    assert warnings == ["SAM.gov returned UEI OTHER1234567 for the requested ALPHA1234567"]
    by = _by_field(suggestions)
    assert by["sam_status"][0].value == "inactive" and "sam_expires_on" not in by
    assert by["legal_structure"][0].value == "llp"
    assert "addresses[]" not in by  # unknown country
    assert by["codes.naics[]"][0].value == {"code": "541330", "is_primary": True}


def test_filter_for_region_drops_the_other_regions_fields() -> None:
    def s(field: str) -> Suggestion:
        return Suggestion(field=field, value="v", source="website", source_ref="u", confidence=0.5)

    suggestions = [
        s("legal_name"),
        s("uei"),
        s("cage_code"),
        s("codes.naics[]"),
        s("codes.psc[]"),
        s("codes.gem[]"),
        s("gstin"),
        s("service_lines[]"),
    ]
    # NAICS / PSC / ALN are US schemes, GeM / India categories are IN schemes (profile_fields)
    kept, dropped = filter_for_region(suggestions, Region.IN)
    assert [x.field for x in kept] == ["legal_name", "codes.gem[]", "gstin", "service_lines[]"]
    assert [x.field for x in dropped] == ["uei", "cage_code", "codes.naics[]", "codes.psc[]"]
    kept, dropped = filter_for_region(suggestions, "us")
    assert [x.field for x in dropped] == ["codes.gem[]", "gstin"]
    assert [x.field for x in kept] == [
        "legal_name",
        "uei",
        "cage_code",
        "codes.naics[]",
        "codes.psc[]",
        "service_lines[]",
    ]
