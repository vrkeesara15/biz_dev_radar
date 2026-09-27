"""Plain-value views of a company profile and an opportunity for matching (SPEC 6).

    profile = MatchProfile(region="us", codes={"naics": ("541511",)}, ...)
    opp = MatchOpportunity(region="us", country="US", notice_type="rfp", ...)

Built by app.services.matching.loaders from ORM rows so core.matching stays I/O-free.
Every collection is a tuple (hashable, immutable); money is Decimal; datetimes are aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.eligibility_in import ProfileSnapshotIn


@dataclass(frozen=True, slots=True)
class KeywordWeight:
    term: str
    weight: Decimal = Decimal("1.0")


@dataclass(frozen=True, slots=True)
class HeldCertification:
    kind: str  # app.core.profile_fields.CertificationKind value
    expires_on: date | None = None

    def valid_on(self, day: date) -> bool:
        return self.expires_on is None or self.expires_on >= day


@dataclass(frozen=True, slots=True)
class HeldRegistration:
    kind: str  # app.core.profile_fields.RegistrationKind value (sam, dsc, gem, cppp, state_portal)
    identifier: str | None = None
    expires_on: date | None = None

    def valid_on(self, day: date) -> bool:
        return self.expires_on is None or self.expires_on >= day


@dataclass(frozen=True, slots=True)
class MatchProfile:
    """What matching needs to know about one company profile (SPEC 4.1-4.6)."""

    region: str  # us | in
    id: str | None = None
    version: int = 1
    year_founded: int | None = None
    # where and how big (SPEC 4.4)
    target_countries: tuple[str, ...] = ()
    target_us_states: tuple[str, ...] = ()
    target_in_states: tuple[str, ...] = ()
    target_cities: tuple[str, ...] = ()
    remote_ok: bool = False
    target_buyers: tuple[str, ...] = ()
    blocked_buyers: tuple[str, ...] = ()
    value_min_usd: Decimal | None = None
    value_max_usd: Decimal | None = None
    value_min_inr: Decimal | None = None
    value_max_inr: Decimal | None = None
    notice_types_wanted: tuple[str, ...] = ()
    # what we sell (SPEC 4.3): {scheme: codes} with schemes naics|psc|aln|gem|india_category
    codes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    include_keywords: tuple[KeywordWeight, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    # size and status (SPEC 4.2): USD receipts average for SBA size, head count
    avg_receipts_usd: Decimal | None = None
    employee_count_total: int | None = None
    certifications: tuple[HeldCertification, ...] = ()
    registrations: tuple[HeldRegistration, ...] = ()
    sam_status: str | None = None
    sam_expires_on: date | None = None
    # India statuses
    udyam_number: str | None = None
    udyam_category: str | None = None  # micro | small | medium
    mse_ownership: str | None = None  # none | sc_st | women | sc_st_women
    dpiit_number: str | None = None
    gem_seller_id: str | None = None
    local_supplier_class: str | None = None  # class_1 | class_2 | non_local
    # proof (SPEC 4.5): customers named in past performance records (buyer affinity)
    past_customers: tuple[str, ...] = ()
    # full IN eligibility input for core.eligibility_in (None for US profiles)
    eligibility_in: ProfileSnapshotIn | None = None
    # SPEC 6 stage-2 weights (validated per profile; defaults in core.preferences)
    scoring_weights: dict[str, int] = field(default_factory=dict)

    def codes_for(self, scheme: str) -> tuple[str, ...]:
        return self.codes.get(scheme, ())

    @property
    def is_mse(self) -> bool:
        """Udyam-registered micro or small enterprise (medium is not an MSE)."""
        return bool(self.udyam_number and self.udyam_number.strip()) and self.udyam_category in (
            "micro",
            "small",
        )

    def has_certification(self, kind: str, on: date) -> bool:
        return any(c.kind == kind and c.valid_on(on) for c in self.certifications)


@dataclass(frozen=True, slots=True)
class MatchOpportunity:
    """What matching needs to know about one notice (SPEC 5.3)."""

    region: str  # us | in
    country: str
    notice_type: str
    title: str
    id: str | None = None
    version: int = 1
    summary: str | None = None  # summary_ai, else description_text
    status: str = "open"
    currency: str = "USD"
    buyer_org: str | None = None
    buyer_sub_org: str | None = None
    buyer_office: str | None = None
    buyer_hierarchy: tuple[str, ...] = ()
    naics: tuple[str, ...] = ()
    psc: tuple[str, ...] = ()
    aln: tuple[str, ...] = ()
    india_category: tuple[str, ...] = ()
    set_aside: str | None = None  # SAM code (SBA, 8A, HZC, WOSB, ...) upper-case
    reservation: str | None = None  # Indian reservation text (MSE, SC/ST, women, startup)
    place_of_performance: dict[str, Any] | None = None
    estimated_value_min: Decimal | None = None
    estimated_value_max: Decimal | None = None
    estimated_value_min_usd: Decimal | None = None
    estimated_value_max_usd: Decimal | None = None
    response_due_at: datetime | None = None
    eligibility: dict[str, Any] = field(default_factory=dict)
    incumbent: str | None = None
    # an awards-enrichment row flagged this notice as a recompete to watch (SPEC 5.2)
    recompete_watch: bool = False

    @property
    def buyers(self) -> tuple[str, ...]:
        """Every buyer name we know, most specific last."""
        names: list[str] = []
        for name in (self.buyer_org, self.buyer_sub_org, self.buyer_office, *self.buyer_hierarchy):
            if name and name not in names:
                names.append(name)
        return tuple(names)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.summary or ''}"
