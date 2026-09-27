"""Reading matches back for the API (SPEC 10.3: "GET /opportunities/{id} ... with match").

    best = await best_matches(session, [opportunity_id, ...])
    stmt = with_min_score(stmt, 70)

Opportunities are global but matches are tenant-scoped, so both helpers run on the
caller's session and RLS decides what they can see. A tenant may have several profiles
scoring the same notice; the one the UI shows is the BEST of them (highest score, then
the newest opportunity and profile version), because that is the answer to "should we
look at this?".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, Opportunity


def with_min_score(stmt: Select[Any], min_score: int | None) -> Select[Any]:
    """Keep only notices the caller's tenant scored at least this high (SPEC 10.3)."""
    if min_score is None:
        return stmt
    return stmt.where(
        exists().where(Match.opportunity_id == Opportunity.id, Match.score >= min_score)
    )


async def best_matches(
    session: AsyncSession, opportunity_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Match]:
    """{opportunity id: the tenant's best match row}, for the ids that have one."""
    if not opportunity_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Match)
                .where(Match.opportunity_id.in_(list(opportunity_ids)))
                .order_by(
                    Match.opportunity_id,
                    Match.score.desc(),
                    Match.opportunity_version.desc(),
                    Match.profile_version.desc(),
                    Match.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    best: dict[uuid.UUID, Match] = {}
    for row in rows:  # ordered best-first per opportunity, so the first wins
        best.setdefault(row.opportunity_id, row)
    return best


def match_out(row: Match) -> dict[str, Any]:
    """The `match` block of an opportunity response (M4-16 reads exactly this)."""
    return {
        "id": str(row.id),
        "profile_id": str(row.profile_id),
        "score": float(row.score),
        "band": row.band,
        "opportunity_version": row.opportunity_version,
        "profile_version": row.profile_version,
        "ineligible_set_aside": row.ineligible_set_aside,
        "filtered_reason": row.filtered_reason,
        "breakdown": dict(row.breakdown or {}),
        "rationale": row.rationale,
        "created_at": row.created_at.isoformat(),
    }


__all__ = ["best_matches", "match_out", "with_min_score"]
