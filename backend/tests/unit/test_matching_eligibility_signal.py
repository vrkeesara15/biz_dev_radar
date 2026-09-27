"""M4-04: eligibility signal from extracted criteria (US: SBA size, receipts, experience,
certifications, registrations; IN: core.eligibility_in). Missing data = 0.5 + unknown."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.core.eligibility_in import CertificationIn, ProfileSnapshotIn, RegistrationIn
from app.core.finance import FiscalYearRevenue
from app.core.matching.eligibility_signal import (
    CriteriaUS,
    Criterion,
    eligibility_signal,
    evaluate_india,
    evaluate_us,
)
from app.core.matching.engine import evaluate
from app.core.matching.types import (
    HeldCertification,
    HeldRegistration,
    MatchOpportunity,
    MatchProfile,
)

D = Decimal
TODAY = date(2026, 9, 27)
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

US_PROFILE = MatchProfile(
    region="us",
    year_founded=2015,
    avg_receipts_usd=D("20000000"),
    employee_count_total=120,
    certifications=(HeldCertification("iso_9001", date(2027, 1, 1)), HeldCertification("cmmc")),
    registrations=(HeldRegistration("state_portal", "VA-eVA", date(2027, 5, 1)),),
    sam_status="active",
    sam_expires_on=date(2027, 3, 1),
)

US_OPP = MatchOpportunity(
    region="us",
    country="US",
    notice_type="rfp",
    title="x",
    naics=("541511",),
    response_due_at=NOW + timedelta(days=30),
)


def _statuses(results: list[Criterion]) -> dict[str, str]:
    return {r.name: r.status.value for r in results}


# --- US criteria loader ------------------------------------------------------------------------


def test_criteria_us_from_dict_is_tolerant() -> None:
    criteria = CriteriaUS.from_dict(
        {
            "requires_small_business": "yes",
            "min_annual_revenue_usd": "$5M",
            "min_experience_years": "3",
            "required_certifications": ["ISO 9001", "iso-9001", "CMMC", "  "],
            "required_registrations": ["SAM", " state_portal ", ""],
            "something_else": 1,
        }
    )
    assert criteria.requires_small_business is True
    assert criteria.min_avg_receipts_usd == D("5000000")
    assert criteria.min_experience_years == 3
    assert criteria.required_certifications == ("iso_9001", "cmmc")
    assert criteria.required_registrations == ("sam", "state_portal")
    assert criteria.empty is False
    assert CriteriaUS.from_dict({}).empty is True
    assert CriteriaUS.from_dict({"min_avg_receipts_usd": 1500000.5}).min_avg_receipts_usd == D(
        "1500000.5"
    )
    assert CriteriaUS.from_dict({"min_avg_receipts_usd": D("7")}).min_avg_receipts_usd == D("7")
    assert CriteriaUS.from_dict({"requires_small_business": False}).requires_small_business is False
    assert CriteriaUS.from_dict({"min_experience_years": True}).min_experience_years is None


# --- US path: size status ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile_kwargs", "opp_kwargs", "eligibility", "status", "fragment"),
    [
        # explicit flag
        ({}, {}, {"requires_small_business": True}, "pass", "small under NAICS 541511"),
        ({"avg_receipts_usd": D("40000000")}, {}, {"requires_small_business": True}, "fail",
         "other than small"),
        ({"avg_receipts_usd": None}, {}, {"requires_small_business": True}, "unknown",
         "average receipts unknown"),
        ({}, {"naics": ()}, {"requires_small_business": True}, "unknown", "no NAICS"),
        ({}, {"naics": ("999999",)}, {"requires_small_business": True}, "unknown",
         "no SBA size standard"),
        # derived from the set-aside code when the flag is absent
        ({}, {"set_aside": "SBA"}, {}, "pass", "small"),
        ({}, {"set_aside": "8A"}, {}, "pass", "small"),
        ({"avg_receipts_usd": D("40000000")}, {"set_aside": "SBP"}, {}, "fail", "other than small"),
        # several codes: small under any = pass; mixed unknown/other = unknown
        ({"avg_receipts_usd": D("40000000"), "employee_count_total": 1000},
         {"naics": ("541511", "336411")}, {"requires_small_business": True}, "pass",
         "small under NAICS 336411"),
        ({"avg_receipts_usd": D("40000000")}, {"naics": ("541511", "999999")},
         {"requires_small_business": True}, "unknown", "999999"),
    ],
)  # fmt: skip
def test_us_size_status(
    profile_kwargs: dict[str, Any],
    opp_kwargs: dict[str, Any],
    eligibility: dict[str, Any],
    status: str,
    fragment: str,
) -> None:
    profile = replace(US_PROFILE, **profile_kwargs)
    opp = replace(US_OPP, eligibility=eligibility, **opp_kwargs)
    results = evaluate_us(profile, opp, CriteriaUS.from_dict(eligibility), TODAY)
    assert [r.name for r in results] == ["size_status"]
    assert results[0].status.value == status
    assert fragment in results[0].reason


def test_size_status_not_evaluated_without_a_small_business_requirement() -> None:
    assert evaluate_us(US_PROFILE, US_OPP, CriteriaUS(), TODAY) == []
    assert (
        evaluate_us(
            US_PROFILE,
            replace(US_OPP, set_aside="SBA"),
            CriteriaUS(requires_small_business=False),
            TODAY,
        )
        == []
    )
    # unknown programmes do not imply small business
    assert evaluate_us(US_PROFILE, replace(US_OPP, set_aside="LAS"), CriteriaUS(), TODAY) == []


# --- US path: receipts, experience, certifications, registrations -----------------------------


@pytest.mark.parametrize(
    ("receipts", "minimum", "status", "measured"),
    [
        (D("20000000"), "10000000", "pass", "20000000"),
        (D("20000000"), "20000000", "pass", "20000000"),
        (D("20000000"), "20000001", "fail", "20000000"),
        (None, "1", "unknown", None),
    ],
)
def test_us_receipts(
    receipts: Decimal | None, minimum: str, status: str, measured: str | None
) -> None:
    profile = replace(US_PROFILE, avg_receipts_usd=receipts)
    results = evaluate_us(profile, US_OPP, CriteriaUS(min_avg_receipts_usd=D(minimum)), TODAY)
    assert _statuses(results) == {"turnover": status}
    assert results[0].required == minimum and results[0].measured == measured
    if status != "unknown":
        assert "$" in results[0].reason


@pytest.mark.parametrize(
    ("year_founded", "minimum", "status", "measured"),
    [
        (2015, 5, "pass", "11"),
        (2015, 11, "pass", "11"),
        (2015, 12, "fail", "11"),
        (None, 3, "unknown", None),
        (2030, 0, "pass", "0"),
    ],
)
def test_us_experience(
    year_founded: int | None, minimum: int, status: str, measured: str | None
) -> None:
    profile = replace(US_PROFILE, year_founded=year_founded)
    results = evaluate_us(profile, US_OPP, CriteriaUS(min_experience_years=minimum), TODAY)
    assert _statuses(results) == {"experience": status}
    assert results[0].measured == measured


def test_us_certifications() -> None:
    profile = replace(
        US_PROFILE,
        certifications=(
            HeldCertification("iso_9001", date(2027, 1, 1)),
            HeldCertification("cmmc"),
            HeldCertification("fedramp", date(2026, 1, 1)),
            HeldCertification("fedramp", date(2025, 1, 1)),
        ),
    )
    criteria = CriteriaUS.from_dict(
        {"required_certifications": ["ISO 9001", "cmmc", "FedRAMP", "soc2"]}
    )
    results = evaluate_us(profile, US_OPP, criteria, TODAY)
    assert _statuses(results) == {
        "certification:iso_9001": "pass",
        "certification:cmmc": "pass",
        "certification:fedramp": "fail",
        "certification:soc2": "fail",
    }
    by_name = {r.name: r for r in results}
    assert by_name["certification:iso_9001"].reason == "iso_9001 on profile, valid until 2027-01-01"
    assert by_name["certification:iso_9001"].measured == "2027-01-01"
    assert by_name["certification:cmmc"].reason == "cmmc on profile"
    assert by_name["certification:fedramp"].reason == "fedramp expired on 2026-01-01"
    assert by_name["certification:soc2"].reason == "soc2 not on profile"


@pytest.mark.parametrize(
    ("profile_kwargs", "status", "fragment"),
    [
        ({}, "pass", "active, valid until 2027-03-01"),
        ({"sam_expires_on": None}, "pass", "active"),
        ({"sam_status": "inactive"}, "fail", "is inactive"),
        ({"sam_status": "expired"}, "fail", "is expired"),
        ({"sam_expires_on": date(2026, 1, 1)}, "fail", "expired on 2026-01-01"),
        # no status column: fall back to registrations rows
        ({"sam_status": None, "registrations": ()}, "unknown", "not on profile"),
        ({"sam_status": None, "registrations": (HeldRegistration("sam", "X", date(2027, 1, 1)),)},
         "pass", "on profile"),
        ({"sam_status": None, "registrations": (HeldRegistration("sam", "X", date(2026, 1, 1)),)},
         "fail", "expired on 2026-01-01"),
    ],
)  # fmt: skip
def test_us_sam_registration(profile_kwargs: dict[str, Any], status: str, fragment: str) -> None:
    profile = replace(US_PROFILE, **profile_kwargs)
    results = evaluate_us(profile, US_OPP, CriteriaUS(required_registrations=("sam",)), TODAY)
    assert _statuses(results) == {"registration:sam": status}
    assert fragment in results[0].reason


def test_us_other_registrations() -> None:
    criteria = CriteriaUS(required_registrations=("state_portal", "cppp"))
    results = evaluate_us(US_PROFILE, US_OPP, criteria, TODAY)
    assert _statuses(results) == {
        "registration:state_portal": "pass",
        "registration:cppp": "fail",
    }
    expired = replace(
        US_PROFILE, registrations=(HeldRegistration("state_portal", "X", date(2026, 1, 1)),)
    )
    results = evaluate_us(
        expired, US_OPP, CriteriaUS(required_registrations=("state_portal",)), TODAY
    )
    assert results[0].status.value == "fail" and "expired on 2026-01-01" in results[0].reason
    assert results[0].measured == "2026-01-01"


# --- the signal: averaging, unknowns, breakdown --------------------------------------------------


def test_signal_averages_pass_unknown_fail_and_lists_criteria() -> None:
    opp = replace(
        US_OPP,
        set_aside="SBA",
        eligibility={
            "min_avg_receipts_usd": "50000000",  # fail
            "min_experience_years": 3,  # pass
            "required_certifications": ["cmmc"],  # pass
            "required_registrations": ["sam", "cppp"],  # pass, fail
        },
    )
    profile = replace(US_PROFILE, year_founded=None)  # experience unknown
    signal = eligibility_signal(profile, opp, TODAY)
    # size pass 1, turnover fail 0, experience unknown 0.5, cmmc pass 1, sam pass 1, cppp fail 0
    assert signal.raw == D("0.5833")
    assert signal.note == "6 criteria: 3 pass, 2 fail, 1 unknown"
    criteria = signal.detail["criteria"]
    assert [c["name"] for c in criteria] == [
        "size_status",
        "turnover",
        "experience",
        "certification:cmmc",
        "registration:sam",
        "registration:cppp",
    ]
    assert {c["status"] for c in criteria} == {"pass", "fail", "unknown"}
    assert set(criteria[0]) == {"name", "status", "reason", "required", "measured"}


def test_no_criteria_on_the_notice_is_unknown_half() -> None:
    signal = eligibility_signal(US_PROFILE, US_OPP, TODAY)
    assert signal.raw == D("0.5")
    assert signal.note == "1 criteria: 0 pass, 0 fail, 1 unknown"
    assert signal.detail["criteria"] == [
        {
            "name": "criteria",
            "status": "unknown",
            "reason": "no eligibility criteria extracted",
            "required": None,
            "measured": None,
        }
    ]


def test_empty_profile_against_full_criteria_is_all_unknown_or_fail() -> None:
    empty = MatchProfile(region="us")
    opp = replace(
        US_OPP,
        eligibility={
            "requires_small_business": True,
            "min_avg_receipts_usd": 1,
            "min_experience_years": 1,
            "required_registrations": ["sam"],
        },
    )
    signal = eligibility_signal(empty, opp, TODAY)
    assert signal.raw == D("0.5")  # every criterion unknown: missing data on the profile side
    assert {c["status"] for c in signal.detail["criteria"]} == {"unknown"}


# --- IN path -------------------------------------------------------------------------------------

CR = D("10000000")
IN_SNAPSHOT = ProfileSnapshotIn(
    revenue=(
        FiscalYearRevenue(2023, 1 * CR, "INR"),
        FiscalYearRevenue(2024, 2 * CR, "INR"),
        FiscalYearRevenue(2025, 3 * CR, "INR"),
    ),
    year_founded=2015,
    udyam_number="UDYAM-TS-01-0001234",
    udyam_category="small",
    gem_seller_id="GEM-1",
    certifications=(CertificationIn("iso_9001", date(2027, 1, 1)),),
    registrations=(RegistrationIn("dsc", "DSC-1", date(2026, 10, 15)),),
)
IN_PROFILE = MatchProfile(region="in", year_founded=2015, eligibility_in=IN_SNAPSHOT)
IN_OPP = MatchOpportunity(
    region="in",
    country="IN",
    notice_type="gem_bid",
    title="x",
    currency="INR",
    response_due_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
    eligibility={
        "min_avg_turnover_inr": "Rs. 1.5 Cr",
        "min_experience_years": 5,
        "required_certifications": ["ISO 9001", "ISO 27001"],
        "requires_gem_registration": True,
        "requires_dsc": "yes",
        "emd_amount_inr": 50000,
    },
)


def test_in_path_uses_evaluate_in_and_drops_informational_emd() -> None:
    results = evaluate_india(IN_PROFILE, IN_OPP, TODAY)
    assert _statuses(results) == {
        "turnover": "pass",  # 2 Cr average vs 1.5 Cr
        "experience": "pass",
        "certification:iso_9001": "pass",
        "certification:iso_27001": "fail",
        "gem_registration": "pass",
        # the DSC expires 2026-10-15, before the bid due date taken from response_due_at
        "dsc": "fail",
    }
    assert all(r.name != "emd" for r in results)
    signal = eligibility_signal(IN_PROFILE, IN_OPP, TODAY)
    assert signal.raw == D("0.6667")  # 4 pass, 2 fail
    assert signal.note == "6 criteria: 4 pass, 2 fail, 0 unknown"


def test_in_path_keeps_an_explicit_due_on_and_handles_missing_snapshot() -> None:
    explicit = replace(IN_OPP, eligibility={**IN_OPP.eligibility, "due_on": "2026-10-01"})
    results = evaluate_india(IN_PROFILE, explicit, TODAY)
    assert _statuses(results)["dsc"] == "pass"  # valid until 2026-10-15 >= 2026-10-01
    # no eligibility snapshot on the profile: everything data-driven is unknown or fail
    bare = MatchProfile(region="in", year_founded=2015)
    signal = eligibility_signal(bare, IN_OPP, TODAY)
    by_name = {c["name"]: c["status"] for c in signal.detail["criteria"]}
    assert by_name["turnover"] == "unknown"
    assert by_name["experience"] == "pass"  # year_founded still known
    assert by_name["dsc"] == "unknown"
    assert by_name["gem_registration"] == "fail"
    no_due = replace(IN_OPP, response_due_at=None)
    assert _statuses(evaluate_india(IN_PROFILE, no_due, TODAY))["dsc"] == "pass"


def test_in_notice_without_criteria_is_unknown_half() -> None:
    signal = eligibility_signal(IN_PROFILE, replace(IN_OPP, eligibility={}), TODAY)
    assert signal.raw == D("0.5")
    assert signal.detail["criteria"][0]["name"] == "criteria"


# --- engine wiring -------------------------------------------------------------------------------


def test_engine_computes_the_eligibility_signal_and_surfaces_criteria() -> None:
    opp = replace(US_OPP, eligibility={"required_certifications": ["cmmc"]})
    outcome = evaluate(US_PROFILE, opp, NOW)
    assert outcome.result is not None
    assert outcome.breakdown["signals"]["eligibility"]["raw"] == 1.0
    assert outcome.breakdown["signals"]["eligibility"]["weighted"] == 15.0
    assert outcome.breakdown["eligibility"] == [
        {
            "name": "certification:cmmc",
            "status": "pass",
            "reason": "cmmc on profile",
            "required": "cmmc",
            "measured": None,
        }
    ]
