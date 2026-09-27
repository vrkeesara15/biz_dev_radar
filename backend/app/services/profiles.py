"""Company-profile persistence helpers shared by the API and later milestones."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import is_masked
from app.core.plan import Resource
from app.core.preferences import DEFAULT_SCORING_WEIGHTS
from app.core.profile_completeness import ProfileCompleteness, ProfileSnapshot, completeness
from app.core.profile_fields import ENCRYPTED_FIELDS, CodeScheme, KeywordKind, ProfileFileKind
from app.models import (
    BoilerplateBlock,
    Certification,
    CompanyProfile,
    Insurance,
    PastPerformance,
    Personnel,
    ProfileCode,
    ProfileFile,
    ProfileKeyword,
    RateCardEntry,
    ServiceLine,
    UserNotificationPrefs,
)


async def count_profiles(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    total = (
        await session.execute(
            select(func.count())
            .select_from(CompanyProfile)
            .where(CompanyProfile.tenant_id == tenant_id)
        )
    ).scalar_one()
    return int(total)


# Register with PlanService so the profiles limit counts real rows, not ledger entries.
PROFILE_COUNTERS = {Resource.PROFILES.value: count_profiles}


def apply_changes(row: CompanyProfile, changes: Mapping[str, Any]) -> list[str]:
    """Set attributes from validated changes; masked encrypted values are ignored.
    Returns the names that were written (never their values: some are secrets)."""
    written: list[str] = []
    for name, value in changes.items():
        if name in ENCRYPTED_FIELDS and is_masked(value):
            continue
        setattr(row, name, value)
        written.append(name)
    if written:
        row.version = (row.version or 1) + 1
    return sorted(written)


async def naics_codes_for(session: AsyncSession, profile_id: uuid.UUID) -> list[str]:
    """The profile's NAICS codes, primary first (for size status and matching)."""
    rows = await session.execute(
        select(ProfileCode.code)
        .where(ProfileCode.profile_id == profile_id, ProfileCode.scheme == CodeScheme.NAICS)
        .order_by(ProfileCode.is_primary.desc(), ProfileCode.code)
    )
    return [str(code) for code in rows.scalars()]


async def _count(session: AsyncSession, model: Any, *where: Any) -> int:
    total = (
        await session.execute(select(func.count()).select_from(model).where(*where))
    ).scalar_one()
    return int(total)


async def load_snapshot(session: AsyncSession, profile: CompanyProfile) -> ProfileSnapshot:
    """Plain-value view of a profile and its children for core.profile_completeness."""
    pid = profile.id
    code_rows = await session.execute(
        select(ProfileCode.scheme, func.count(), func.bool_or(ProfileCode.is_primary))
        .where(ProfileCode.profile_id == pid)
        .group_by(ProfileCode.scheme)
    )
    codes_by_scheme: dict[str, int] = {}
    has_primary = False
    for row in code_rows.all():
        codes_by_scheme[str(CodeScheme(str(row[0].value)).value)] = int(row[1])
        has_primary = has_primary or bool(row[2])
    keyword_rows = await session.execute(
        select(ProfileKeyword.kind, func.count())
        .where(ProfileKeyword.profile_id == pid)
        .group_by(ProfileKeyword.kind)
    )
    keywords = {
        str(KeywordKind(str(row[0].value)).value): int(row[1]) for row in keyword_rows.all()
    }
    return ProfileSnapshot(
        region=profile.region,
        legal_name=profile.legal_name,
        address_count=len(profile.addresses or []),
        website=profile.website,
        phone=profile.phone,
        bid_inbox_email=profile.bid_inbox_email,
        year_founded=profile.year_founded,
        legal_structure=None if profile.legal_structure is None else str(profile.legal_structure),
        uei=profile.uei,
        cage_code=profile.cage_code,
        sam_status=None if profile.sam_status is None else str(profile.sam_status),
        sam_expires_on=profile.sam_expires_on,
        pan=profile.pan,
        gstin=profile.gstin,
        cin_llpin=profile.cin_llpin,
        udyam_number=profile.udyam_number,
        dpiit_number=profile.dpiit_number,
        gem_seller_id=profile.gem_seller_id,
        employee_count_total=profile.employee_count_total,
        revenue_years=len({e.get("fiscal_year") for e in profile.annual_revenue or []}),
        bonding_capacity_amount=profile.bonding_capacity_amount,
        audited_fiscal_years=len(profile.audited_fiscal_years or []),
        net_worth_amount=profile.net_worth_amount,
        solvency_certificate_available=bool(profile.solvency_certificate_available),
        codes_by_scheme=codes_by_scheme,
        has_primary_code=has_primary,
        include_keywords=keywords.get("include", 0),
        exclude_keywords=keywords.get("exclude", 0),
        service_lines=await _count(session, ServiceLine, ServiceLine.profile_id == pid),
        capability_statement_files=await _count(
            session,
            ProfileFile,
            ProfileFile.profile_id == pid,
            ProfileFile.kind == ProfileFileKind.CAPABILITY_STATEMENT,
        ),
        target_countries=len(profile.target_countries or []),
        target_us_states=len(profile.target_us_states or []),
        target_in_states=len(profile.target_in_states or []),
        remote_ok=bool(profile.remote_ok),
        value_min_usd=profile.value_min_usd,
        value_max_usd=profile.value_max_usd,
        value_min_inr=profile.value_min_inr,
        value_max_inr=profile.value_max_inr,
        notice_types_wanted=len(profile.notice_types_wanted or []),
        target_buyers=len(profile.target_buyers or []),
        blocked_buyers=len(profile.blocked_buyers or []),
        past_performance_count=await _count(
            session, PastPerformance, PastPerformance.profile_id == pid
        ),
        personnel_count=await _count(session, Personnel, Personnel.profile_id == pid),
        boilerplate_count=await _count(
            session, BoilerplateBlock, BoilerplateBlock.profile_id == pid
        ),
        certification_count=await _count(session, Certification, Certification.profile_id == pid),
        insurance_count=await _count(session, Insurance, Insurance.profile_id == pid),
        rate_card_count=await _count(session, RateCardEntry, RateCardEntry.profile_id == pid),
        scoring_weights_customized=dict(profile.scoring_weights or {}) != DEFAULT_SCORING_WEIGHTS,
        required_approver_roles=len(profile.required_approver_roles or []),
        output_languages=len(profile.output_languages or []),
        notification_prefs_saved=await _count(
            session, UserNotificationPrefs, UserNotificationPrefs.tenant_id == profile.tenant_id
        )
        > 0,
    )


async def profile_completeness(
    session: AsyncSession, profile: CompanyProfile
) -> ProfileCompleteness:
    return completeness(await load_snapshot(session, profile))
