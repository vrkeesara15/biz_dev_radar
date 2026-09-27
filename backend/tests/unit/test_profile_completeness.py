"""M1-08: deterministic completeness for empty, half-filled and full profiles (US and IN)."""

from decimal import Decimal

from app.core.config import Region
from app.core.profile_completeness import (
    DRAFTING_THRESHOLD,
    MATCHING_THRESHOLD,
    SECTION_ITEMS,
    SECTION_WEIGHTS,
    ProfileSnapshot,
    completeness,
)


def full(region: Region, **overrides: object) -> ProfileSnapshot:
    values: dict[str, object] = {
        "region": region,
        "legal_name": "Alpha",
        "address_count": 1,
        "website": "https://alpha.example",
        "phone": "+1",
        "bid_inbox_email": "bids@alpha.example",
        "year_founded": 2009,
        "legal_structure": "llc",
        "uei": "ABC123DEF456",
        "cage_code": "1AB23",
        "sam_status": "active",
        "sam_expires_on": "2027-01-01",
        "pan": "ABCDE1234F",
        "gstin": "27ABCDE1234F1Z5",
        "cin_llpin": "U7",
        "udyam_number": "UDYAM",
        "gem_seller_id": "GEM",
        "employee_count_total": 120,
        "revenue_years": 3,
        "bonding_capacity_amount": Decimal("5"),
        "audited_fiscal_years": 3,
        "net_worth_amount": Decimal("1"),
        "solvency_certificate_available": True,
        "codes_by_scheme": {"naics": 3, "psc": 2, "gem": 2, "india_category": 1},
        "has_primary_code": True,
        "include_keywords": 5,
        "exclude_keywords": 1,
        "service_lines": 3,
        "capability_statement_files": 1,
        "target_us_states": 2,
        "target_in_states": 2,
        "value_min_usd": 1,
        "value_max_usd": 2,
        "value_min_inr": 1,
        "value_max_inr": 2,
        "notice_types_wanted": 3,
        "target_buyers": 1,
        "past_performance_count": 5,
        "personnel_count": 2,
        "boilerplate_count": 3,
        "certification_count": 1,
        "rate_card_count": 1,
        "scoring_weights_customized": True,
        "required_approver_roles": 1,
        "output_languages": 1,
        "notification_prefs_saved": True,
    }
    values.update(overrides)
    return ProfileSnapshot(**values)  # type: ignore[arg-type]


def test_weights_and_thresholds() -> None:
    assert sum(SECTION_WEIGHTS.values()) == 100
    assert set(SECTION_ITEMS) == set(SECTION_WEIGHTS)
    assert (MATCHING_THRESHOLD, DRAFTING_THRESHOLD) == (40, 70)


def test_empty_profile_scores_zero() -> None:
    for region in (Region.US, Region.IN):
        result = completeness(ProfileSnapshot(region=region))
        assert result.score == 0
        assert not result.matching_enabled and not result.drafting_enabled
        assert all(s.score == 0 for s in result.sections.values())
        assert (
            "identity.legal_name" in result.missing
            and "proof.past_performance_1_3_5" in result.missing
        )
        assert result.as_dict()["sections"]["proof"]["weight"] == 20


def test_full_profile_scores_100_in_both_regions() -> None:
    for region in (Region.US, Region.IN):
        result = completeness(full(region))
        assert result.score == 100, (region, result.missing)
        assert result.missing == []
        assert result.matching_enabled and result.drafting_enabled
        assert {n: s.score for n, s in result.sections.items()} == SECTION_WEIGHTS


def test_region_specific_items_only_count_for_that_region() -> None:
    # a US profile without any Indian registration is still complete
    us = completeness(
        full(
            Region.US,
            pan=None,
            gstin=None,
            cin_llpin=None,
            udyam_number=None,
            gem_seller_id=None,
            net_worth_amount=None,
            solvency_certificate_available=False,
            codes_by_scheme={"naics": 3},
            target_in_states=0,
            value_min_inr=None,
            value_max_inr=None,
        )
    )
    assert us.score == 100 and us.missing == []
    # and an Indian profile without UEI/CAGE/SAM or NAICS is complete too
    india = completeness(
        full(
            Region.IN,
            uei=None,
            cage_code=None,
            sam_status=None,
            sam_expires_on=None,
            codes_by_scheme={"gem": 3},
            target_us_states=0,
            value_min_usd=None,
            value_max_usd=None,
        )
    )
    assert india.score == 100 and india.missing == []
    # the same missing UEI costs a US profile but not an Indian one
    assert completeness(full(Region.US, uei=None)).sections["registrations"].score == 6
    assert completeness(full(Region.IN, uei=None)).sections["registrations"].score == 10
    # NAICS codes do not satisfy an Indian profile's "what we sell"
    assert completeness(full(Region.IN, codes_by_scheme={"naics": 5})).missing == [
        "what_we_sell.primary_india_category",
        "what_we_sell.codes_3",
    ]


def test_half_filled_profile_is_deterministic() -> None:
    half = ProfileSnapshot(
        region=Region.US,
        legal_name="Alpha",
        address_count=1,
        website="https://alpha.example",
        uei="ABC123DEF456",
        sam_status="active",
        employee_count_total=40,
        revenue_years=2,
        codes_by_scheme={"naics": 2},
        has_primary_code=True,
        include_keywords=3,
        service_lines=1,
        target_us_states=1,
        notice_types_wanted=2,
        past_performance_count=2,
        personnel_count=1,
        output_languages=1,
        required_approver_roles=1,
    )
    result = completeness(half)
    earned = {n: round(s.earned, 3) for n, s in result.sections.items()}
    assert earned == {
        "identity": 8.0,
        "registrations": 6.0,
        "size_finance": 7.0,
        "what_we_sell": 9.8,
        "where_how_big": 5.0,
        "proof": 4.5,
        "preferences": 4.0,
    }
    assert result.score == 44  # 44.3 rounded
    assert result.matching_enabled and not result.drafting_enabled
    assert "proof.past_performance_1_3_5" in result.missing
    assert completeness(half) == result  # deterministic


def test_drafting_needs_70_and_three_past_performances() -> None:
    strong_but_no_pp = full(Region.US, past_performance_count=2)
    result = completeness(strong_but_no_pp)
    assert result.score >= 70 and not result.drafting_enabled
    enough_pp_low_score = ProfileSnapshot(
        region=Region.US, legal_name="x", past_performance_count=3
    )
    result = completeness(enough_pp_low_score)
    assert result.score < 70 and not result.drafting_enabled and not result.matching_enabled
    just_enough = full(
        Region.US,
        past_performance_count=3,
        boilerplate_count=0,
        service_lines=0,
        include_keywords=0,
    )
    result = completeness(just_enough)
    assert 70 <= result.score < 100 and result.drafting_enabled


def test_partial_credit_steps() -> None:
    base = full(Region.US)
    scores = [
        completeness(full(Region.US, past_performance_count=n)).sections["proof"].earned
        for n in (0, 1, 3, 5)
    ]
    assert scores == [11.0, 14.0, 17.0, 20.0]
    assert completeness(full(Region.US, service_lines=2)).sections["what_we_sell"].earned == 18.0
    assert completeness(base).score == 100
