"""Profile completeness score 0-100 (SPEC 4.6). Pure: works on a ProfileSnapshot.

Sections and weights (sum 100):

    identity        15   legal name, address, website, phone, bid inbox, year founded,
                         legal structure
    registrations   10   US: UEI, SAM status + expiry, CAGE       IN: PAN, GSTIN, CIN/LLPIN,
                         (EIN optional, not counted)              Udyam or DPIIT, GeM seller id
    size_finance    15   employee count, 3 FY revenue, bonding/BG limit, audited FYs;
                         IN adds net worth + solvency flag
    what_we_sell    20   primary codes (US: NAICS, IN: GeM/India category), >= 3 codes,
                         include keywords >= 5, exclude keywords >= 1, service lines >= 1
                         (>= 3 for full marks), capability statement file
    where_how_big   10   target geography (states/countries or remote), value range in the
                         region currency, notice types wanted, buyers or blocked buyers
    proof           20   past performance 1 / 3 / 5 records, personnel >= 2, boilerplate
                         >= 3 blocks, certifications or insurance, rate card
    preferences     10   scoring weights reviewed (non-default) or accepted, approvers set,
                         output languages, notification prefs saved

Items that belong to the other region are simply absent from the item list, so a US
profile is never penalized for missing a PAN and an IN profile never for a UEI. Each
section score = earned / possible x weight; the total is the rounded sum.

    matching_enabled  score >= 40
    drafting_enabled  score >= 70 and past_performance_count >= 3
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.core.config import Region
from app.core.profile_fields import MIN_PAST_PERFORMANCE_FOR_DRAFTING

MATCHING_THRESHOLD = 40
DRAFTING_THRESHOLD = 70

SECTION_WEIGHTS: dict[str, int] = {
    "identity": 15,
    "registrations": 10,
    "size_finance": 15,
    "what_we_sell": 20,
    "where_how_big": 10,
    "proof": 20,
    "preferences": 10,
}


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """Everything completeness needs, as plain values (built by services.profiles)."""

    region: Region
    # identity
    legal_name: str | None = None
    address_count: int = 0
    website: str | None = None
    phone: str | None = None
    bid_inbox_email: str | None = None
    year_founded: int | None = None
    legal_structure: str | None = None
    # registrations (US)
    uei: str | None = None
    cage_code: str | None = None
    sam_status: str | None = None
    sam_expires_on: object | None = None
    # registrations (IN)
    pan: str | None = None
    gstin: str | None = None
    cin_llpin: str | None = None
    udyam_number: str | None = None
    dpiit_number: str | None = None
    gem_seller_id: str | None = None
    # size / finance
    employee_count_total: int | None = None
    revenue_years: int = 0
    bonding_capacity_amount: object | None = None
    audited_fiscal_years: int = 0
    net_worth_amount: object | None = None
    solvency_certificate_available: bool = False
    # what we sell
    codes_by_scheme: dict[str, int] = field(default_factory=dict)
    has_primary_code: bool = False
    include_keywords: int = 0
    exclude_keywords: int = 0
    service_lines: int = 0
    capability_statement_files: int = 0
    # where / how big
    target_countries: int = 0
    target_us_states: int = 0
    target_in_states: int = 0
    remote_ok: bool = False
    value_min_usd: object | None = None
    value_max_usd: object | None = None
    value_min_inr: object | None = None
    value_max_inr: object | None = None
    notice_types_wanted: int = 0
    target_buyers: int = 0
    blocked_buyers: int = 0
    # proof
    past_performance_count: int = 0
    personnel_count: int = 0
    boilerplate_count: int = 0
    certification_count: int = 0
    insurance_count: int = 0
    rate_card_count: int = 0
    # preferences
    scoring_weights_customized: bool = False
    required_approver_roles: int = 0
    output_languages: int = 0
    notification_prefs_saved: bool = False


@dataclass(frozen=True, slots=True)
class Item:
    name: str
    points: int
    check: Callable[[ProfileSnapshot], float]  # 0..1 fraction earned


@dataclass(frozen=True, slots=True)
class SectionScore:
    name: str
    weight: int
    earned: float  # 0..weight
    possible: int  # item points
    missing: list[str]

    @property
    def score(self) -> int:
        return round(self.earned)

    @property
    def ratio(self) -> float:
        return self.earned / self.weight if self.weight else 0.0


@dataclass(frozen=True, slots=True)
class ProfileCompleteness:
    score: int
    sections: dict[str, SectionScore]
    matching_enabled: bool
    drafting_enabled: bool
    missing: list[str]
    past_performance_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "matching_enabled": self.matching_enabled,
            "drafting_enabled": self.drafting_enabled,
            "missing": list(self.missing),
            "sections": {
                name: {
                    "score": s.score,
                    "weight": s.weight,
                    "missing": list(s.missing),
                }
                for name, s in self.sections.items()
            },
        }


def _present(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        return 1.0 if value.strip() else 0.0
    return 1.0


def _at_least(count: int, wanted: int) -> float:
    if wanted <= 0:
        return 1.0
    return min(count, wanted) / wanted


def _steps(count: int, *thresholds: int) -> float:
    """Fraction of thresholds reached: _steps(4, 1, 3, 5) == 2/3."""
    reached = sum(1 for t in thresholds if count >= t)
    return reached / len(thresholds)


def _both(a: object, b: object) -> float:
    return 1.0 if a is not None and b is not None else 0.0


def identity_items(region: Region) -> list[Item]:
    return [
        Item("legal_name", 3, lambda s: _present(s.legal_name)),
        Item("address", 3, lambda s: _at_least(s.address_count, 1)),
        Item("website", 2, lambda s: _present(s.website)),
        Item("phone", 2, lambda s: _present(s.phone)),
        Item("bid_inbox_email", 2, lambda s: _present(s.bid_inbox_email)),
        Item("year_founded", 1, lambda s: _present(s.year_founded)),
        Item("legal_structure", 2, lambda s: _present(s.legal_structure)),
    ]


def registration_items(region: Region) -> list[Item]:
    if region is Region.US:
        return [
            Item("uei", 4, lambda s: _present(s.uei)),
            Item("sam_status", 2, lambda s: _present(s.sam_status)),
            Item("sam_expires_on", 2, lambda s: _present(s.sam_expires_on)),
            Item("cage_code", 2, lambda s: _present(s.cage_code)),
        ]
    return [
        Item("pan", 3, lambda s: _present(s.pan)),
        Item("gstin", 3, lambda s: _present(s.gstin)),
        Item("cin_llpin", 1, lambda s: _present(s.cin_llpin)),
        Item(
            "udyam_or_dpiit",
            1,
            lambda s: max(_present(s.udyam_number), _present(s.dpiit_number)),
        ),
        Item("gem_seller_id", 2, lambda s: _present(s.gem_seller_id)),
    ]


def size_finance_items(region: Region) -> list[Item]:
    items = [
        Item("employee_count_total", 3, lambda s: _present(s.employee_count_total)),
        Item("annual_revenue_3fy", 6, lambda s: _at_least(s.revenue_years, 3)),
        Item("bonding_capacity", 3, lambda s: _present(s.bonding_capacity_amount)),
        Item("audited_fiscal_years", 3, lambda s: _at_least(s.audited_fiscal_years, 1)),
    ]
    if region is Region.IN:
        items += [
            Item("net_worth", 2, lambda s: _present(s.net_worth_amount)),
            Item(
                "solvency_certificate",
                1,
                lambda s: 1.0 if s.solvency_certificate_available else 0.0,
            ),
        ]
    return items


def _region_codes(s: ProfileSnapshot) -> int:
    if s.region is Region.US:
        return s.codes_by_scheme.get("naics", 0)
    return s.codes_by_scheme.get("gem", 0) + s.codes_by_scheme.get("india_category", 0)


def what_we_sell_items(region: Region) -> list[Item]:
    primary = "primary_naics" if region is Region.US else "primary_india_category"
    return [
        Item(primary, 4, lambda s: 1.0 if s.has_primary_code and _region_codes(s) else 0.0),
        Item("codes_3", 3, lambda s: _at_least(_region_codes(s), 3)),
        Item("include_keywords_5", 3, lambda s: _at_least(s.include_keywords, 5)),
        Item("exclude_keywords_1", 1, lambda s: _at_least(s.exclude_keywords, 1)),
        Item("service_lines", 6, lambda s: _steps(s.service_lines, 1, 2, 3)),
        Item("capability_statement", 3, lambda s: _at_least(s.capability_statement_files, 1)),
    ]


def _geography(s: ProfileSnapshot) -> float:
    if s.remote_ok or s.target_countries:
        return 1.0
    states = s.target_us_states if s.region is Region.US else s.target_in_states
    return 1.0 if states else 0.0


def _value_range(s: ProfileSnapshot) -> float:
    if s.region is Region.US:
        return _both(s.value_min_usd, s.value_max_usd)
    return _both(s.value_min_inr, s.value_max_inr)


def where_how_big_items(region: Region) -> list[Item]:
    return [
        Item("target_geography", 3, _geography),
        Item("value_range", 3, _value_range),
        Item("notice_types_wanted", 2, lambda s: _at_least(s.notice_types_wanted, 1)),
        Item("buyers", 2, lambda s: _at_least(s.target_buyers + s.blocked_buyers, 1)),
    ]


def proof_items(region: Region) -> list[Item]:
    return [
        Item("past_performance_1_3_5", 9, lambda s: _steps(s.past_performance_count, 1, 3, 5)),
        Item("personnel_2", 3, lambda s: _at_least(s.personnel_count, 2)),
        Item("boilerplate_3", 4, lambda s: _at_least(s.boilerplate_count, 3)),
        Item(
            "certifications_or_insurance",
            2,
            lambda s: _at_least(s.certification_count + s.insurance_count, 1),
        ),
        Item("rate_card", 2, lambda s: _at_least(s.rate_card_count, 1)),
    ]


def preference_items(region: Region) -> list[Item]:
    return [
        Item("scoring_weights_reviewed", 3, lambda s: 1.0 if s.scoring_weights_customized else 0.0),
        Item("required_approver_roles", 2, lambda s: _at_least(s.required_approver_roles, 1)),
        Item("output_languages", 2, lambda s: _at_least(s.output_languages, 1)),
        Item("notification_prefs", 3, lambda s: 1.0 if s.notification_prefs_saved else 0.0),
    ]


SECTION_ITEMS: dict[str, Callable[[Region], list[Item]]] = {
    "identity": identity_items,
    "registrations": registration_items,
    "size_finance": size_finance_items,
    "what_we_sell": what_we_sell_items,
    "where_how_big": where_how_big_items,
    "proof": proof_items,
    "preferences": preference_items,
}


def score_section(name: str, snapshot: ProfileSnapshot) -> SectionScore:
    weight = SECTION_WEIGHTS[name]
    items = SECTION_ITEMS[name](snapshot.region)
    possible = sum(i.points for i in items)
    earned_points = 0.0
    missing: list[str] = []
    for item in items:
        fraction = max(0.0, min(1.0, item.check(snapshot)))
        earned_points += fraction * item.points
        if fraction < 1.0:
            missing.append(f"{name}.{item.name}")
    earned = earned_points / possible * weight if possible else 0.0
    return SectionScore(name, weight, earned, possible, missing)


def completeness(snapshot: ProfileSnapshot) -> ProfileCompleteness:
    sections = {name: score_section(name, snapshot) for name in SECTION_WEIGHTS}
    total = round(sum(s.earned for s in sections.values()))
    total = max(0, min(100, total))
    missing = [m for s in sections.values() for m in s.missing]
    return ProfileCompleteness(
        score=total,
        sections=sections,
        matching_enabled=total >= MATCHING_THRESHOLD,
        drafting_enabled=total >= DRAFTING_THRESHOLD
        and snapshot.past_performance_count >= MIN_PAST_PERFORMANCE_FOR_DRAFTING,
        missing=missing,
        past_performance_count=snapshot.past_performance_count,
    )


def section_names() -> Iterable[str]:
    return SECTION_WEIGHTS.keys()
