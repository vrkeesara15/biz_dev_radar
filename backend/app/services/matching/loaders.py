"""Build core.matching inputs from ORM rows (the only place matching touches the DB).

profile = await load_match_profile(session, company_profile_row)
opp = match_opportunity_from_row(opportunity_row, recompete_watch=...)
opp = await load_match_opportunity(session, opportunity_row)   # looks up recompete watch
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.finance import FiscalYearRevenue, MixedCurrencyError, average_turnover
from app.core.matching.types import (
    HeldCertification,
    HeldRegistration,
    KeywordWeight,
    MatchOpportunity,
    MatchProfile,
)
from app.core.profile_fields import KeywordKind
from app.models import (
    AwardsEnrichment,
    Certification,
    CompanyProfile,
    Opportunity,
    PastPerformance,
    ProfileCode,
    ProfileKeyword,
    Registration,
)
from app.services.profiles import load_eligibility_snapshot


def usd_receipts(annual_revenue: list[dict[str, Any]] | None) -> Decimal | None:
    """3-FY average turnover in USD for SBA size status; None when unknown or not USD
    (SBA receipts are USD only, see OQ-20)."""
    try:
        avg = average_turnover(
            FiscalYearRevenue(int(e["fiscal_year"]), Decimal(str(e["amount"])), str(e["currency"]))
            for e in (annual_revenue or [])
        )
    except MixedCurrencyError:
        return None
    if avg is None or avg.currency != "USD":
        return None
    return avg.amount


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _tuple(values: list[str] | None) -> tuple[str, ...]:
    return tuple(values or ())


async def load_match_profile(session: AsyncSession, profile: CompanyProfile) -> MatchProfile:
    pid = profile.id
    code_rows = (
        await session.execute(
            select(ProfileCode.scheme, ProfileCode.code)
            .where(ProfileCode.profile_id == pid)
            .order_by(ProfileCode.is_primary.desc(), ProfileCode.code)
        )
    ).all()
    codes: dict[str, list[str]] = {}
    for scheme, code in code_rows:
        codes.setdefault(str(scheme.value), []).append(str(code))
    keyword_rows = (
        await session.execute(
            select(ProfileKeyword.kind, ProfileKeyword.term, ProfileKeyword.weight)
            .where(ProfileKeyword.profile_id == pid)
            .order_by(ProfileKeyword.term)
        )
    ).all()
    include = tuple(
        KeywordWeight(str(term), Decimal(str(weight)))
        for kind, term, weight in keyword_rows
        if kind == KeywordKind.INCLUDE
    )
    exclude = tuple(str(term) for kind, term, _ in keyword_rows if kind == KeywordKind.EXCLUDE)
    cert_rows = (
        await session.execute(
            select(Certification.kind, Certification.expires_on).where(
                Certification.profile_id == pid
            )
        )
    ).all()
    reg_rows = (
        await session.execute(
            select(Registration.kind, Registration.identifier, Registration.expires_on)
            .where(Registration.profile_id == pid)
            .order_by(Registration.created_at)
        )
    ).all()
    customers = (
        await session.execute(
            select(PastPerformance.customer)
            .where(PastPerformance.profile_id == pid)
            .distinct()
            .order_by(PastPerformance.customer)
        )
    ).scalars()
    eligibility_in = (
        await load_eligibility_snapshot(session, profile) if profile.region.value == "in" else None
    )
    return MatchProfile(
        region=profile.region.value,
        id=str(profile.id),
        version=int(profile.version or 1),
        year_founded=profile.year_founded,
        target_countries=_tuple(profile.target_countries),
        target_us_states=_tuple(profile.target_us_states),
        target_in_states=_tuple(profile.target_in_states),
        target_cities=_tuple(profile.target_cities),
        remote_ok=bool(profile.remote_ok),
        target_buyers=_tuple(profile.target_buyers),
        blocked_buyers=_tuple(profile.blocked_buyers),
        value_min_usd=profile.value_min_usd,
        value_max_usd=profile.value_max_usd,
        value_min_inr=profile.value_min_inr,
        value_max_inr=profile.value_max_inr,
        notice_types_wanted=_tuple(profile.notice_types_wanted),
        codes={scheme: tuple(values) for scheme, values in codes.items()},
        include_keywords=include,
        exclude_keywords=exclude,
        avg_receipts_usd=usd_receipts(profile.annual_revenue),
        employee_count_total=profile.employee_count_total,
        certifications=tuple(
            HeldCertification(str(kind.value), expires_on) for kind, expires_on in cert_rows
        ),
        registrations=tuple(
            HeldRegistration(str(kind.value), ident, expires_on)
            for kind, ident, expires_on in reg_rows
        ),
        sam_status=_enum_value(profile.sam_status),
        sam_expires_on=profile.sam_expires_on,
        udyam_number=profile.udyam_number,
        udyam_category=_enum_value(profile.udyam_category),
        mse_ownership=_enum_value(profile.mse_ownership),
        dpiit_number=profile.dpiit_number,
        gem_seller_id=profile.gem_seller_id,
        local_supplier_class=_enum_value(profile.local_supplier_class),
        past_customers=tuple(str(c) for c in customers),
        eligibility_in=eligibility_in,
        scoring_weights=dict(profile.scoring_weights or {}),
    )


def match_opportunity_from_row(
    opp: Opportunity, *, recompete_watch: bool = False
) -> MatchOpportunity:
    return MatchOpportunity(
        region=opp.region.value,
        country=opp.country,
        notice_type=opp.notice_type.value,
        title=opp.title,
        id=str(opp.id),
        version=int(opp.version or 1),
        summary=opp.summary_ai or opp.description_text,
        status=opp.status.value,
        currency=opp.currency,
        buyer_org=opp.buyer_org,
        buyer_sub_org=opp.buyer_sub_org,
        buyer_office=opp.buyer_office,
        buyer_hierarchy=_tuple(opp.buyer_hierarchy),
        naics=_tuple(opp.naics),
        psc=_tuple(opp.psc),
        aln=_tuple(opp.aln),
        india_category=_tuple(opp.india_category),
        set_aside=opp.set_aside,
        reservation=opp.reservation,
        place_of_performance=dict(opp.place_of_performance) if opp.place_of_performance else None,
        estimated_value_min=opp.estimated_value_min,
        estimated_value_max=opp.estimated_value_max,
        estimated_value_min_usd=opp.estimated_value_min_usd,
        estimated_value_max_usd=opp.estimated_value_max_usd,
        response_due_at=opp.response_due_at,
        eligibility=dict(opp.eligibility or {}),
        incumbent=opp.incumbent,
        recompete_watch=recompete_watch,
    )


async def is_recompete_watch(session: AsyncSession, opportunity: Opportunity) -> bool:
    """True when an awards-enrichment row flags the notice (or its solicitation) as a
    recompete to watch (SPEC 5.2 USAspending / M2-07)."""
    clauses = [AwardsEnrichment.opportunity_id == opportunity.id]
    if opportunity.solicitation_number:
        clauses.append(AwardsEnrichment.solicitation_number == opportunity.solicitation_number)
    stmt = select(
        exists().where(
            AwardsEnrichment.recompete_watch.is_(True),
            clauses[0] if len(clauses) == 1 else (clauses[0] | clauses[1]),
        )
    )
    return bool((await session.execute(stmt)).scalar())


async def load_match_opportunity(session: AsyncSession, opp: Opportunity) -> MatchOpportunity:
    return match_opportunity_from_row(opp, recompete_watch=await is_recompete_watch(session, opp))
