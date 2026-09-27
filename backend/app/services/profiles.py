"""Company-profile persistence helpers shared by the API and later milestones."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import is_masked
from app.core.plan import Resource
from app.core.profile_fields import ENCRYPTED_FIELDS
from app.models import CompanyProfile


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
