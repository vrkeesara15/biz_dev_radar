"""M3-08: India eligibility rules (turnover, experience, EMD, MSE/Udyam and Startup exemptions,
certifications, GeM seller ID, DSC). Table-driven over every branch of core.eligibility_in."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from app.core.eligibility_in import (
    MSE_EXEMPTION,
    STARTUP_EXEMPTION,
    CertificationIn,
    CriteriaIn,
    CriterionResult,
    EligibilityIn,
    ProfileSnapshotIn,
    RegistrationIn,
    Status,
    evaluate_in,
    is_mse,
    is_startup,
    normalize_certification,
    snapshot_from_values,
)
from app.core.finance import FiscalYearRevenue

D = Decimal
TODAY = date(2026, 9, 27)
CR = D("10000000")


def fy(year: int, crore: str, currency: str = "INR") -> FiscalYearRevenue:
    return FiscalYearRevenue(year, D(crore) * CR, currency)


THREE_FY = (fy(2023, "1"), fy(2024, "2"), fy(2025, "3"))  # average 2 Cr


def profile(**overrides: Any) -> ProfileSnapshotIn:
    return ProfileSnapshotIn(**overrides)


def mse(category: str = "small", **overrides: Any) -> ProfileSnapshotIn:
    return profile(udyam_number="UDYAM-TN-01-0001234", udyam_category=category, **overrides)


def startup(**overrides: Any) -> ProfileSnapshotIn:
    return profile(dpiit_number="DIPP12345", **overrides)


def one(results: EligibilityIn, name: str) -> CriterionResult:
    matches = [r for r in results.results if r.name == name]
    assert len(matches) == 1, [r.name for r in results.results]
    return matches[0]


def names(results: EligibilityIn) -> list[str]:
    return [r.name for r in results.results]


# --- flags ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "expected_mse", "expected_startup"),
    [
        (profile(), False, False),
        (mse("micro"), True, False),
        (mse("small"), True, False),
        (mse("medium"), False, False),
        (profile(udyam_number="UDYAM-X", udyam_category=None), False, False),
        (profile(udyam_number=None, udyam_category="micro"), False, False),
        (profile(udyam_number="  ", udyam_category="micro"), False, False),
        (startup(), False, True),
        (profile(dpiit_number=""), False, False),
        (mse("micro", dpiit_number="DIPP1"), True, True),
    ],
)
def test_mse_and_startup_flags(
    snapshot: ProfileSnapshotIn, expected_mse: bool, expected_startup: bool
) -> None:
    assert is_mse(snapshot) is expected_mse
    assert is_startup(snapshot) is expected_startup


# --- turnover ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "criteria", "status", "exemption", "reason_has"),
    [
        # average of the last 3 FY in INR vs the minimum
        (
            profile(revenue=THREE_FY),
            CriteriaIn(min_avg_turnover_inr=D("15000000")),
            "pass",
            None,
            "₹2 Cr",
        ),
        (
            profile(revenue=THREE_FY),
            CriteriaIn(min_avg_turnover_inr=2 * CR),
            "pass",
            None,
            "FY2023-2025",
        ),
        (
            profile(revenue=THREE_FY),
            CriteriaIn(min_avg_turnover_inr=D("25000000")),
            "fail",
            None,
            "₹2.5 Cr",
        ),
        # older years outside the window do not count
        (
            profile(revenue=(*THREE_FY, fy(2020, "100"))),
            CriteriaIn(min_avg_turnover_inr=3 * CR),
            "fail",
            None,
            "₹2 Cr",
        ),
        # missing years -> unknown (SPEC 6: missing data = 0.5)
        (
            profile(revenue=THREE_FY[1:]),
            CriteriaIn(min_avg_turnover_inr=CR),
            "unknown",
            None,
            "2 of 3",
        ),
        (profile(), CriteriaIn(min_avg_turnover_inr=CR), "unknown", None, "no annual revenue"),
        (
            profile(revenue=(fy(2023, "1", "USD"), fy(2024, "1", "USD"), fy(2025, "1", "USD"))),
            CriteriaIn(min_avg_turnover_inr=CR),
            "unknown",
            None,
            "USD",
        ),
        (
            profile(revenue=(fy(2023, "1"), fy(2024, "1", "USD"), fy(2025, "1"))),
            CriteriaIn(min_avg_turnover_inr=CR),
            "unknown",
            None,
            "currencies",
        ),
        # tender-specific window
        (
            profile(revenue=THREE_FY[1:]),
            CriteriaIn(min_avg_turnover_inr=CR, turnover_years=2),
            "pass",
            None,
            "FY2024-2025",
        ),
        # Udyam micro/small: relaxed only when the tender allows it
        (
            mse("small"),
            CriteriaIn(min_avg_turnover_inr=CR, allows_mse_exemption=True),
            "pass",
            MSE_EXEMPTION,
            "Udyam",
        ),
        (
            mse("micro", revenue=THREE_FY),
            CriteriaIn(min_avg_turnover_inr=5 * CR, allows_mse_exemption=True),
            "pass",
            MSE_EXEMPTION,
            "relaxed",
        ),
        (
            mse("medium"),
            CriteriaIn(min_avg_turnover_inr=CR, allows_mse_exemption=True),
            "unknown",
            None,
            "no annual revenue",
        ),
        (
            mse("small", revenue=THREE_FY),
            CriteriaIn(min_avg_turnover_inr=5 * CR),
            "fail",
            None,
            "not offered",
        ),
        # DPIIT startup: relaxed only when the tender flag allows
        (
            startup(),
            CriteriaIn(min_avg_turnover_inr=CR, allows_startup_exemption=True),
            "pass",
            STARTUP_EXEMPTION,
            "DPIIT",
        ),
        (
            startup(revenue=THREE_FY),
            CriteriaIn(min_avg_turnover_inr=5 * CR),
            "fail",
            None,
            "not offered",
        ),
        (
            startup(),
            CriteriaIn(min_avg_turnover_inr=CR, allows_mse_exemption=True),
            "unknown",
            None,
            "no annual revenue",
        ),
        # both statuses and both flags: the MSE exemption is reported
        (
            mse("micro", dpiit_number="D1"),
            CriteriaIn(
                min_avg_turnover_inr=CR, allows_mse_exemption=True, allows_startup_exemption=True
            ),
            "pass",
            MSE_EXEMPTION,
            "Udyam",
        ),
        (
            mse("micro", dpiit_number="D1"),
            CriteriaIn(min_avg_turnover_inr=CR, allows_startup_exemption=True),
            "pass",
            STARTUP_EXEMPTION,
            "DPIIT",
        ),
    ],
)
def test_turnover(
    snapshot: ProfileSnapshotIn,
    criteria: CriteriaIn,
    status: str,
    exemption: str | None,
    reason_has: str,
) -> None:
    result = one(evaluate_in(snapshot, criteria, TODAY), "turnover")
    assert result.status == Status(status)
    assert result.exemption_applied == exemption
    assert reason_has in result.reason, result.reason
    assert result.blocking is False
    assert result.required == str(criteria.min_avg_turnover_inr)


def test_turnover_measured_value_and_absence() -> None:
    result = one(
        evaluate_in(profile(revenue=THREE_FY), CriteriaIn(min_avg_turnover_inr=CR), TODAY),
        "turnover",
    )
    assert result.measured == "20000000.00"
    assert "turnover" not in names(evaluate_in(profile(revenue=THREE_FY), CriteriaIn(), TODAY))


# --- experience ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "criteria", "status", "exemption", "measured"),
    [
        (profile(year_founded=2015), CriteriaIn(min_experience_years=5), "pass", None, "11"),
        (profile(year_founded=2021), CriteriaIn(min_experience_years=5), "pass", None, "5"),
        (profile(year_founded=2023), CriteriaIn(min_experience_years=5), "fail", None, "3"),
        (
            profile(year_founded=2023, experience_years=7),
            CriteriaIn(min_experience_years=5),
            "pass",
            None,
            "7",
        ),
        (profile(), CriteriaIn(min_experience_years=5), "unknown", None, None),
        (
            mse("micro", year_founded=2025),
            CriteriaIn(min_experience_years=5, allows_mse_exemption=True),
            "pass",
            MSE_EXEMPTION,
            "1",
        ),
        (mse("micro", year_founded=2025), CriteriaIn(min_experience_years=5), "fail", None, "1"),
        (
            startup(),
            CriteriaIn(min_experience_years=5, allows_startup_exemption=True),
            "pass",
            STARTUP_EXEMPTION,
            None,
        ),
        (startup(), CriteriaIn(min_experience_years=5), "unknown", None, None),
    ],
)
def test_experience(
    snapshot: ProfileSnapshotIn,
    criteria: CriteriaIn,
    status: str,
    exemption: str | None,
    measured: str | None,
) -> None:
    result = one(evaluate_in(snapshot, criteria, TODAY), "experience")
    assert (result.status, result.exemption_applied, result.measured) == (
        Status(status),
        exemption,
        measured,
    )
    assert result.required == "5"
    assert "experience" not in names(evaluate_in(snapshot, CriteriaIn(), TODAY))


# --- EMD -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "criteria", "exemption", "reason_has"),
    [
        (profile(), CriteriaIn(emd_amount_inr=D("50000")), None, "₹50,000 payable"),
        (
            mse("small"),
            CriteriaIn(emd_amount_inr=D("50000"), allows_mse_exemption=True),
            MSE_EXEMPTION,
            "waived",
        ),
        (mse("micro"), CriteriaIn(emd_amount_inr=D("50000")), None, "not offered"),
        (
            mse("medium"),
            CriteriaIn(emd_amount_inr=D("50000"), allows_mse_exemption=True),
            None,
            "payable",
        ),
        (
            startup(),
            CriteriaIn(emd_amount_inr=D("250000"), allows_startup_exemption=True),
            STARTUP_EXEMPTION,
            "₹2.5 Lakh waived",
        ),
        (startup(), CriteriaIn(emd_amount_inr=D("250000")), None, "not offered"),
    ],
)
def test_emd(
    snapshot: ProfileSnapshotIn, criteria: CriteriaIn, exemption: str | None, reason_has: str
) -> None:
    result = one(evaluate_in(snapshot, criteria, TODAY), "emd")
    assert result.status is Status.PASS and result.informational is True
    assert result.exemption_applied == exemption
    assert reason_has in result.reason, result.reason
    assert result.required == str(criteria.emd_amount_inr)


def test_emd_absent_or_zero_is_not_a_criterion() -> None:
    assert "emd" not in names(evaluate_in(mse(), CriteriaIn(emd_amount_inr=D("0")), TODAY))
    assert "emd" not in names(evaluate_in(mse(), CriteriaIn(), TODAY))


# --- certifications ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("ISO 9001", "iso_9001"),
        ("iso-9001:2015", "iso_9001_2015"),
        ("ISO27001", "iso_27001"),
        ("  CMMi ", "cmmi"),
        ("CERT-In", "cert_in"),
        ("8(a)", "8a"),
        ("SOC 2", "soc2"),  # canonical CertificationKind wins
        ("soc-2", "soc2"),
        ("stqc", "stqc"),
    ],
)
def test_normalize_certification(raw: str, normalized: str) -> None:
    assert normalize_certification(raw) == normalized


VALID_ISO = CertificationIn("iso_9001", date(2027, 1, 1))
EXPIRED_ISO = CertificationIn("iso_9001", date(2026, 1, 1))


@pytest.mark.parametrize(
    ("certs", "required", "expected"),
    [
        (
            (VALID_ISO, CertificationIn("iso_27001")),
            ("ISO 9001", "iso_27001"),
            {"certification:iso_9001": "pass", "certification:iso_27001": "pass"},
        ),
        ((EXPIRED_ISO,), ("iso_9001",), {"certification:iso_9001": "fail"}),
        ((), ("ISO 9001",), {"certification:iso_9001": "fail"}),
        ((CertificationIn("iso_9001", TODAY),), ("iso_9001",), {"certification:iso_9001": "pass"}),
        ((EXPIRED_ISO, VALID_ISO), ("iso_9001",), {"certification:iso_9001": "pass"}),
        (
            (VALID_ISO,),
            ("ISO 9001", "iso 9001"),
            {"certification:iso_9001": "pass"},
        ),  # de-duplicated
        ((VALID_ISO,), (), {}),
    ],
)
def test_certifications(
    certs: tuple[CertificationIn, ...], required: tuple[str, ...], expected: dict[str, str]
) -> None:
    results = evaluate_in(
        profile(certifications=certs), CriteriaIn(required_certifications=required), TODAY
    )
    got = {r.name: r.status.value for r in results.results if r.name.startswith("certification:")}
    assert got == expected
    for r in results.results:
        assert r.blocking is False and r.exemption_applied is None


def test_certification_reasons() -> None:
    results = evaluate_in(
        profile(certifications=(EXPIRED_ISO,)),
        CriteriaIn(required_certifications=("iso_9001", "cmmi")),
        TODAY,
    )
    assert "expired on 2026-01-01" in one(results, "certification:iso_9001").reason
    assert "not on profile" in one(results, "certification:cmmi").reason
    ok = one(
        evaluate_in(
            profile(certifications=(VALID_ISO,)),
            CriteriaIn(required_certifications=("iso_9001",)),
            TODAY,
        ),
        "certification:iso_9001",
    )
    assert ok.measured == "2027-01-01" and ok.required == "iso_9001"


# --- GeM seller ID -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("snapshot", "status", "blocking", "reason_has"),
    [
        (profile(gem_seller_id="GEM-SELLER-1"), "pass", False, "GEM-SELLER-1"),
        (
            profile(registrations=(RegistrationIn("gem", identifier="GEM-2"),)),
            "pass",
            False,
            "GEM-2",
        ),
        (
            profile(
                registrations=(
                    RegistrationIn("gem", identifier="GEM-2", expires_on=date(2027, 1, 1)),
                )
            ),
            "pass",
            False,
            "GEM-2",
        ),
        (profile(), "fail", True, "GeM seller ID missing"),
        (profile(gem_seller_id="  "), "fail", True, "missing"),
        (profile(registrations=(RegistrationIn("gem"),)), "fail", True, "missing"),
        (
            profile(
                registrations=(
                    RegistrationIn("gem", identifier="GEM-2", expires_on=date(2026, 1, 1)),
                )
            ),
            "fail",
            True,
            "expired on 2026-01-01",
        ),
        (
            profile(
                gem_seller_id="GEM-1",
                registrations=(
                    RegistrationIn("gem", identifier="GEM-2", expires_on=date(2026, 1, 1)),
                ),
            ),
            "pass",
            False,
            "GEM-1",
        ),
    ],
)
def test_gem_registration(
    snapshot: ProfileSnapshotIn, status: str, blocking: bool, reason_has: str
) -> None:
    result = one(
        evaluate_in(snapshot, CriteriaIn(requires_gem_registration=True), TODAY), "gem_registration"
    )
    assert (result.status, result.blocking) == (Status(status), blocking)
    assert reason_has in result.reason, result.reason
    assert "gem_registration" not in names(evaluate_in(snapshot, CriteriaIn(), TODAY))


# --- DSC -----------------------------------------------------------------------------------


def dsc(expires: date | None) -> RegistrationIn:
    return RegistrationIn("dsc", identifier="Ravi Kumar", expires_on=expires)


@pytest.mark.parametrize(
    ("registrations", "due_on", "status", "blocking", "reason_has"),
    [
        ((dsc(date(2026, 11, 30)),), None, "pass", False, "valid until 2026-11-30"),
        ((dsc(TODAY),), None, "pass", False, "valid until"),
        ((dsc(date(2026, 1, 1)),), None, "fail", True, "expired on 2026-01-01"),
        (
            (dsc(date(2026, 11, 30)),),
            date(2026, 12, 15),
            "fail",
            True,
            "before the bid due date 2026-12-15",
        ),
        ((dsc(date(2026, 12, 15)),), date(2026, 12, 15), "pass", False, "valid until"),
        ((), None, "unknown", False, "no Class 3 DSC on profile"),
        ((dsc(None),), None, "unknown", False, "expiry not recorded"),
        ((dsc(date(2026, 1, 1)), dsc(date(2027, 1, 1))), None, "pass", False, "2027-01-01"),
        ((dsc(date(2026, 1, 1)), dsc(None)), None, "unknown", False, "expiry not recorded"),
        ((RegistrationIn("gem", identifier="x"),), None, "unknown", False, "no Class 3 DSC"),
    ],
)
def test_dsc(
    registrations: tuple[RegistrationIn, ...],
    due_on: date | None,
    status: str,
    blocking: bool,
    reason_has: str,
) -> None:
    snapshot = profile(registrations=registrations)
    result = one(evaluate_in(snapshot, CriteriaIn(requires_dsc=True, due_on=due_on), TODAY), "dsc")
    assert (result.status, result.blocking) == (Status(status), blocking)
    assert reason_has in result.reason, result.reason
    assert "dsc" not in names(evaluate_in(snapshot, CriteriaIn(due_on=due_on), TODAY))


# --- overall -------------------------------------------------------------------------------

FULL_CRITERIA = CriteriaIn(
    min_avg_turnover_inr=CR,
    min_experience_years=3,
    required_certifications=("ISO 9001",),
    emd_amount_inr=D("50000"),
    requires_gem_registration=True,
    requires_dsc=True,
    due_on=date(2026, 10, 30),
)
FULL_PROFILE = profile(
    revenue=THREE_FY,
    year_founded=2015,
    gem_seller_id="GEM-1",
    certifications=(VALID_ISO,),
    registrations=(dsc(date(2026, 11, 30)),),
)


def test_overall_pass() -> None:
    out = evaluate_in(FULL_PROFILE, FULL_CRITERIA, TODAY)
    assert names(out) == [
        "turnover",
        "experience",
        "emd",
        "certification:iso_9001",
        "gem_registration",
        "dsc",
    ]
    assert out.status is Status.PASS and out.score == D("1")
    assert out.blocking == () and out.exemptions == ()


def test_overall_fail_with_blocking_and_exemptions() -> None:
    snapshot = mse("micro", registrations=(dsc(date(2026, 1, 1)),))
    out = evaluate_in(
        snapshot,
        CriteriaIn(
            min_avg_turnover_inr=CR,
            min_experience_years=3,
            required_certifications=("cmmi",),
            emd_amount_inr=D("50000"),
            allows_mse_exemption=True,
            requires_gem_registration=True,
            requires_dsc=True,
        ),
        TODAY,
    )
    assert out.status is Status.FAIL
    assert out.blocking == ("gem_registration", "dsc")
    assert out.exemptions == (MSE_EXEMPTION,)
    # turnover pass, experience pass (relaxed), cert fail, gem fail, dsc fail; EMD informational
    assert out.score == D("0.4")


def test_overall_unknown_and_score_arithmetic() -> None:
    out = evaluate_in(
        profile(year_founded=2015),
        CriteriaIn(min_avg_turnover_inr=CR, min_experience_years=3, emd_amount_inr=D("1")),
        TODAY,
    )
    assert [r.status.value for r in out.results] == ["unknown", "pass", "pass"]
    assert out.status is Status.UNKNOWN
    assert out.score == D("0.75")  # EMD does not count toward the signal


def test_no_criteria_is_a_pass() -> None:
    out = evaluate_in(profile(), CriteriaIn(), TODAY)
    assert out.results == () and out.status is Status.PASS and out.score == D("1")


def test_today_defaults_to_current_date() -> None:
    out = evaluate_in(
        profile(registrations=(dsc(date(2099, 1, 1)),)), CriteriaIn(requires_dsc=True)
    )
    assert one(out, "dsc").status is Status.PASS


def test_as_dict_is_json_safe() -> None:
    out = evaluate_in(FULL_PROFILE, FULL_CRITERIA, TODAY).as_dict()
    assert out["status"] == "pass" and out["score"] == "1"
    assert out["blocking"] == [] and out["exemptions"] == []
    first = out["results"][0]
    assert first == {
        "name": "turnover",
        "status": "pass",
        "reason": first["reason"],
        "exemption_applied": None,
        "blocking": False,
        "informational": False,
        "required": "10000000",
        "measured": "20000000.00",
    }
    import json

    json.dumps(out)


# --- criteria from the extracted eligibility jsonb (SPEC 5.3) ------------------------------


def test_criteria_from_dict() -> None:
    criteria = CriteriaIn.from_dict(
        {
            "min_avg_turnover_inr": "Rs. 1.5 Cr",
            "turnover_years": "3",
            "min_experience_years": 5.0,
            "required_certifications": ["ISO 9001", "ISO 9001", "CMMI"],
            "emd_amount_inr": 50000,
            "allows_mse_exemption": "true",
            "allows_startup_exemption": False,
            "requires_gem_registration": 1,
            "requires_dsc": None,
            "due_on": "2026-10-30",
            "unknown_key": "ignored",
        }
    )
    assert criteria == CriteriaIn(
        min_avg_turnover_inr=D("15000000"),
        turnover_years=3,
        min_experience_years=5,
        required_certifications=("iso_9001", "cmmi"),
        emd_amount_inr=D("50000"),
        allows_mse_exemption=True,
        allows_startup_exemption=False,
        requires_gem_registration=True,
        requires_dsc=False,
        due_on=date(2026, 10, 30),
    )
    assert CriteriaIn.from_dict({}) == CriteriaIn()
    assert (
        CriteriaIn.from_dict({"min_avg_turnover_inr": "as per tender", "due_on": "soon"})
        == CriteriaIn()
    )
    assert CriteriaIn.from_dict({"due_on": date(2026, 1, 1)}).due_on == date(2026, 1, 1)
    assert CriteriaIn.from_dict({"min_experience_years": "many"}).min_experience_years is None
    assert CriteriaIn.from_dict({"emd_amount_inr": D("5")}).emd_amount_inr == D("5")


def test_criteria_validation() -> None:
    with pytest.raises(ValueError):
        CriteriaIn(turnover_years=0)
    with pytest.raises(ValueError):
        CriteriaIn(min_avg_turnover_inr=D("-1"))
    with pytest.raises(ValueError):
        CriteriaIn(min_experience_years=-1)


# --- snapshot from stored values (thin service adapter feeds this) -------------------------


def test_snapshot_from_values() -> None:
    snapshot = snapshot_from_values(
        annual_revenue=[
            {"fiscal_year": 2025, "amount": "30000000.00", "currency": "INR"},
            {"fiscal_year": 2024, "amount": 20000000, "currency": "INR"},
        ],
        year_founded=2015,
        udyam_number="UDYAM-1",
        udyam_category="micro",
        dpiit_number=None,
        gem_seller_id="GEM-1",
        certifications=[("iso_9001", date(2027, 1, 1)), ("cmmi", None)],
        registrations=[("dsc", "Ravi", date(2026, 11, 30)), ("gem", None, None)],
    )
    assert snapshot == ProfileSnapshotIn(
        revenue=(fy(2025, "3"), fy(2024, "2")),
        year_founded=2015,
        udyam_number="UDYAM-1",
        udyam_category="micro",
        dpiit_number=None,
        gem_seller_id="GEM-1",
        certifications=(CertificationIn("iso_9001", date(2027, 1, 1)), CertificationIn("cmmi")),
        registrations=(
            RegistrationIn("dsc", "Ravi", date(2026, 11, 30)),
            RegistrationIn("gem", None, None),
        ),
    )
    assert is_mse(snapshot)
    assert snapshot_from_values(annual_revenue=None) == ProfileSnapshotIn()
