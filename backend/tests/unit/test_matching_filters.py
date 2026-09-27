"""M4-01: SPEC 6 stage-1 hard filters, table-driven per filter."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.core.matching.filters import (
    INELIGIBLE_SET_ASIDE_CAP,
    INELIGIBLE_SET_ASIDE_LABEL,
    FilterResult,
    hard_filters,
    set_aside_check,
)
from app.core.matching.types import HeldCertification, MatchOpportunity, MatchProfile

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=20)
PAST = NOW - timedelta(days=1)

US_PROFILE = MatchProfile(
    region="us",
    target_countries=("US",),
    notice_types_wanted=("rfp", "rfq", "sources_sought", "combined"),
    blocked_buyers=("Department of Energy", "Acme Corp"),
    exclude_keywords=("janitorial", "snow removal"),
    codes={"naics": ("541511",)},
    avg_receipts_usd=Decimal("20000000"),
    employee_count_total=120,
    certifications=(HeldCertification("wosb"),),
)

US_OPP = MatchOpportunity(
    region="us",
    country="US",
    notice_type="rfp",
    title="Cloud migration services",
    summary="Modernize the agency data centre",
    buyer_org="Department of the Treasury",
    buyer_sub_org="Internal Revenue Service",
    naics=("541511",),
    response_due_at=FUTURE,
)

IN_PROFILE = MatchProfile(
    region="in",
    target_countries=("IN",),
    udyam_number="UDYAM-TS-01-0001234",
    udyam_category="small",
    mse_ownership="women",
    dpiit_number=None,
)

IN_OPP = MatchOpportunity(
    region="in",
    country="IN",
    notice_type="gem_bid",
    title="Supply of laptops",
    response_due_at=FUTURE,
)


def _keep(result: FilterResult) -> bool:
    return result.keep


# --- region / country -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "opp", "keep", "reason"),
    [
        (US_PROFILE, US_OPP, True, None),
        (US_PROFILE, replace(US_OPP, region="in", country="IN"), False, "region"),
        (US_PROFILE, replace(US_OPP, country="CA"), False, "country"),
        # no target countries = every country in the region
        (replace(US_PROFILE, target_countries=()), replace(US_OPP, country="CA"), True, None),
        (replace(US_PROFILE, target_countries=("us",)), US_OPP, True, None),
        (IN_PROFILE, IN_OPP, True, None),
        (IN_PROFILE, replace(IN_OPP, region="us", country="US"), False, "region"),
    ],
    ids=["same", "other-region", "other-country", "any-country", "lower-case", "in-same", "in-us"],
)
def test_region_and_country(
    profile: MatchProfile, opp: MatchOpportunity, keep: bool, reason: str | None
) -> None:
    result = hard_filters(profile, opp, NOW)
    assert result.keep is keep
    assert result.reason == reason


# --- notice type ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wanted", "notice_type", "keep"),
    [
        (("rfp", "rfq"), "rfp", True),
        (("rfp", "rfq"), "grant", False),
        ((), "grant", True),  # nothing chosen = everything wanted
        (("RFP",), "rfp", True),  # tolerant to case
        (("rfp",), "corrigendum", False),
    ],
)
def test_notice_type_wanted(wanted: tuple[str, ...], notice_type: str, keep: bool) -> None:
    profile = replace(US_PROFILE, notice_types_wanted=wanted)
    result = hard_filters(profile, replace(US_OPP, notice_type=notice_type), NOW)
    assert result.keep is keep
    assert result.reason == (None if keep else "notice_type")


# --- blocked buyers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("blocked", "opp_kwargs", "keep"),
    [
        (("Department of Energy",), {"buyer_org": "Department of Energy"}, False),
        # normalised: case, punctuation, legal suffix, leading article
        (("the department of energy, inc.",), {"buyer_org": "DEPARTMENT OF ENERGY"}, False),
        (("Acme Corp",), {"buyer_org": "Acme Corporation"}, False),
        # any level of the hierarchy
        (("Bonneville Power Administration",), {
            "buyer_org": "Department of Energy",
            "buyer_hierarchy": ("Department of Energy", "Bonneville Power Administration"),
        }, False),
        (("Bonneville Power Administration",), {"buyer_office": "Bonneville Power Admin."}, True),
        # a blocked phrase inside a longer office name blocks (whole words only)
        (("Energy",), {"buyer_org": "Department of Energy"}, False),
        (("Energy",), {"buyer_org": "Energetic Systems Agency"}, True),
        (("Department of Energy",), {"buyer_org": "Department of the Treasury"}, True),
        ((), {"buyer_org": "Department of Energy"}, True),
        (("Department of Energy",), {"buyer_org": None}, True),
    ],
)  # fmt: skip
def test_blocked_buyers(blocked: tuple[str, ...], opp_kwargs: dict[str, Any], keep: bool) -> None:
    profile = replace(US_PROFILE, blocked_buyers=blocked)
    blank: dict[str, Any] = {"buyer_org": None, "buyer_sub_org": None, "buyer_office": None}
    opp = replace(US_OPP, **{**blank, **opp_kwargs})
    result = hard_filters(profile, opp, NOW)
    assert result.keep is keep
    assert result.reason == (None if keep else "blocked_buyer")


# --- response date --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("due", "recompete_watch", "status", "keep", "reason"),
    [
        (FUTURE, False, "open", True, None),
        (NOW + timedelta(minutes=1), False, "open", True, None),
        (NOW, False, "open", False, "past_due"),
        (PAST, False, "closed", False, "past_due"),
        (PAST, True, "closed", True, None),  # recompete watch keeps it
        (None, False, "open", True, None),  # unknown deadline is not "past"
        (FUTURE, False, "cancelled", False, "status_cancelled"),
        (FUTURE, False, "awarded", False, "status_awarded"),
        (FUTURE, True, "awarded", True, None),
    ],
)
def test_response_due_in_future_unless_recompete_watch(
    due: datetime | None, recompete_watch: bool, status: str, keep: bool, reason: str | None
) -> None:
    opp = replace(US_OPP, response_due_at=due, recompete_watch=recompete_watch, status=status)
    result = hard_filters(US_PROFILE, opp, NOW)
    assert result.keep is keep
    assert result.reason == reason


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        hard_filters(US_PROFILE, US_OPP, datetime(2026, 9, 27, 12, 0))


# --- exclusion keywords ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("excluded", "title", "summary", "keep", "reason"),
    [
        (("janitorial",), "Janitorial services for building 4", None, False,
         "exclusion_keyword:janitorial"),
        (("janitorial",), "Cloud services", "Includes JANITORIAL support", False,
         "exclusion_keyword:janitorial"),
        (("snow removal",), "Snow  removal and salting", None, False,
         "exclusion_keyword:snow removal"),
        # whole words: "snowplow" does not match "snow"
        (("snow",), "Snowplow procurement", None, True, None),
        (("janitorial",), "Cloud migration", "Data centre work", True, None),
        ((), "Janitorial services", None, True, None),
        (("  ",), "Janitorial services", None, True, None),  # blank terms are ignored
    ],
)  # fmt: skip
def test_exclusion_keywords(
    excluded: tuple[str, ...], title: str, summary: str | None, keep: bool, reason: str | None
) -> None:
    profile = replace(US_PROFILE, exclude_keywords=excluded)
    result = hard_filters(profile, replace(US_OPP, title=title, summary=summary), NOW)
    assert result.keep is keep
    assert result.reason == reason


# --- set-asides: kept, capped at 30, labelled ----------------------------------------------


def _us(
    certs: tuple[str, ...] = (), receipts: str | None = "20000000", employees: int | None = 120
):
    return replace(
        US_PROFILE,
        certifications=tuple(HeldCertification(c) for c in certs),
        avg_receipts_usd=None if receipts is None else Decimal(receipts),
        employee_count_total=employees,
    )


@pytest.mark.parametrize(
    ("profile", "set_aside", "naics", "eligible"),
    [
        # 8(a) family
        (_us(("8a",)), "8A", ("541511",), True),
        (_us(("8a",)), "8AN", ("541511",), True),
        (_us(("wosb",)), "8A", ("541511",), False),
        # HUBZone
        (_us(("hubzone",)), "HZC", ("541511",), True),
        (_us(()), "HZS", ("541511",), False),
        # WOSB / EDWOSB: an EDWOSB qualifies for a WOSB set-aside, not the reverse
        (_us(("wosb",)), "WOSB", ("541511",), True),
        (_us(("edwosb",)), "WOSB", ("541511",), True),
        (_us(("wosb",)), "EDWOSB", ("541511",), False),
        (_us(("edwosb",)), "EDWOSBSS", ("541511",), True),
        # SDVOSB / VOSB: an SDVOSB qualifies for a VOSB set-aside
        (_us(("sdvosb",)), "SDVOSBC", ("541511",), True),
        (_us(("sdvosb",)), "VSA", ("541511",), True),
        (_us(("vosb",)), "SDVOSBS", ("541511",), False),
        (_us(("vosb",)), "VSS", ("541511",), True),
        # total / partial small business: SBA size status for the notice's NAICS
        (_us((), "20000000", 120), "SBA", ("541511",), True),  # $34M cap
        (_us((), "40000000", 120), "SBA", ("541511",), False),
        (_us((), "40000000", 120), "SBP", ("541511",), False),
        (_us((), "40000000", 1000), "SBA", ("336411",), True),  # 1,500 employees cap
        (_us((), "40000000", 2000), "SBA", ("336411",), False),
        # any of several codes small -> eligible
        (_us((), "40000000", 1000), "SBA", ("541511", "336411"), True),
        # unknown size (no finance data, no NAICS, unknown standard) never penalises
        (_us((), None, None), "SBA", ("541511",), True),
        (_us((), "40000000", 120), "SBA", (), True),
        (_us((), "40000000", 120), "SBA", ("999999",), True),
        # 8(a) also expects the company to be small when we can tell
        (_us(("8a",), "40000000", 120), "8A", ("541511",), False),
        # programmes we cannot evaluate from the profile stay uncapped
        (_us(()), "LAS", ("541511",), True),
        (_us(()), "IEE", ("541511",), True),
        (_us(()), "BICIV", ("541511",), True),
        # full and open
        (_us(()), None, ("541511",), True),
        (_us(()), "", ("541511",), True),
        (_us(()), "NONE", ("541511",), True),
    ],
)
def test_us_set_aside(
    profile: MatchProfile, set_aside: str | None, naics: tuple[str, ...], eligible: bool
) -> None:
    opp = replace(US_OPP, set_aside=set_aside, naics=naics)
    result = hard_filters(profile, opp, NOW)
    assert result.keep is True
    assert result.reason is None
    if eligible:
        assert result.cap is None and result.label is None
        assert result.ineligible_set_aside is False
    else:
        assert result.cap == INELIGIBLE_SET_ASIDE_CAP == 30
        assert result.label == INELIGIBLE_SET_ASIDE_LABEL == "Ineligible: set-aside"
        assert result.ineligible_set_aside is True


def test_expired_certification_does_not_count() -> None:
    expired = replace(
        US_PROFILE, certifications=(HeldCertification("8a", expires_on=NOW.date() - timedelta(1)),)
    )
    still_valid = replace(
        US_PROFILE, certifications=(HeldCertification("8a", expires_on=NOW.date()),)
    )
    opp = replace(US_OPP, set_aside="8A")
    assert hard_filters(expired, opp, NOW).cap == 30
    assert hard_filters(still_valid, opp, NOW).cap is None


@pytest.mark.parametrize(
    ("profile", "reservation", "eligible"),
    [
        # MSE reservation: Udyam micro/small; medium is not an MSE
        (IN_PROFILE, "MSE", True),
        (IN_PROFILE, "Reserved for MSEs", True),
        (IN_PROFILE, "MSME only", True),
        (replace(IN_PROFILE, udyam_category="medium"), "MSE", False),
        (replace(IN_PROFILE, udyam_number=None), "Micro and Small Enterprises", False),
        # SC/ST-owned MSE
        (replace(IN_PROFILE, mse_ownership="sc_st"), "SC/ST MSE", True),
        (replace(IN_PROFILE, mse_ownership="sc_st_women"), "SC-ST owned", True),
        (replace(IN_PROFILE, mse_ownership="women"), "SC/ST", False),
        # women-owned MSE
        (replace(IN_PROFILE, mse_ownership="women"), "Women owned MSE", True),
        (replace(IN_PROFILE, mse_ownership="sc_st"), "Women entrepreneurs", False),
        # DPIIT startup
        (replace(IN_PROFILE, dpiit_number="DIPP12345"), "Startup", True),
        (IN_PROFILE, "Startups only", False),
        # Make in India: Class-I local supplier
        (replace(IN_PROFILE, local_supplier_class="class_1"), "Class-I local supplier", True),
        (replace(IN_PROFILE, local_supplier_class="class_2"), "Class I local suppliers", False),
        (replace(IN_PROFILE, local_supplier_class="non_local"), "Make in India", False),
        # open or unrecognised text never caps
        (IN_PROFILE, None, True),
        (IN_PROFILE, "Open", True),
        (IN_PROFILE, "Domestic bidders", True),
    ],
)  # fmt: skip
def test_in_reservation(profile: MatchProfile, reservation: str | None, eligible: bool) -> None:
    result = hard_filters(profile, replace(IN_OPP, reservation=reservation), NOW)
    assert result.keep is True
    if eligible:
        assert result.cap is None and result.ineligible_set_aside is False
    else:
        assert result.cap == 30 and result.label == "Ineligible: set-aside"


def test_set_aside_check_reports_the_missing_status() -> None:
    check = set_aside_check(_us(()), replace(US_OPP, set_aside="8A"), NOW.date())
    assert check.eligible is False
    assert check.code == "8A"
    assert "8(a)" in check.detail
    open_check = set_aside_check(_us(()), US_OPP, NOW.date())
    assert open_check.eligible is True and open_check.code is None


# --- ordering: a drop wins over a cap; the first failing filter names the reason -----------


def test_drop_reasons_take_precedence_over_set_aside_cap() -> None:
    opp = replace(US_OPP, set_aside="8A", response_due_at=PAST)
    result = hard_filters(US_PROFILE, opp, NOW)
    assert result.keep is False and result.reason == "past_due"
    assert result.cap is None and result.ineligible_set_aside is False


def test_filter_result_is_json_safe() -> None:
    result = hard_filters(US_PROFILE, replace(US_OPP, set_aside="8A"), NOW)
    assert result.as_dict() == {
        "keep": True,
        "reason": None,
        "cap": 30,
        "label": "Ineligible: set-aside",
        "ineligible_set_aside": True,
        "checks": result.as_dict()["checks"],
    }
    names = [c["name"] for c in result.as_dict()["checks"]]
    assert names == [
        "region",
        "notice_type",
        "blocked_buyer",
        "response_due",
        "exclusion_keywords",
        "set_aside",
    ]
    assert all(c["passed"] for c in result.as_dict()["checks"][:-1])
    assert result.as_dict()["checks"][-1]["passed"] is False
