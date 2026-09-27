"""Learning loop persistence (M4-07): store thumbs, propose keywords, apply a decision.

    await record_feedback(session, match=match, user_id=..., thumb="down", reason="...")
    suggestions = await retune_profile(session, profile_id, now=..., since=...)
    await decide_suggestion(session, suggestion, status="approved", user_id=...)

Nothing is applied without a decision (SPEC 6: "never silently applied"). Approving an
INCLUDE suggestion adds the keyword or nudges its weight by `delta_weight`; approving an
EXCLUDE suggestion adds an exclusion keyword. Rejecting one only stamps the row, and the
weekly job then leaves that term alone for good.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.matching.learning import (
    EXCLUDE,
    INCLUDE,
    MIN_LIFT,
    MIN_SUPPORT,
    Observation,
    TermLift,
    candidate_terms,
    keyword_lift,
)
from app.core.profile_fields import KeywordKind
from app.models import (
    KeywordSuggestion,
    Match,
    MatchFeedback,
    Opportunity,
    ProfileKeyword,
    SuggestionStatus,
    Thumb,
)

log = structlog.get_logger(__name__)

FEEDBACK_WINDOW_DAYS = 90
MAX_KEYWORD_CHARS = 100
MIN_WEIGHT = Decimal("0.1")
MAX_WEIGHT = Decimal("9.9")


# --- feedback ------------------------------------------------------------------------------


async def record_feedback(
    session: AsyncSession,
    *,
    match: Match,
    user_id: uuid.UUID,
    thumb: str,
    reason: str | None = None,
) -> MatchFeedback:
    """One row per (match, user): a changed mind replaces the earlier thumb."""
    value = Thumb(thumb).value
    row = (
        await session.execute(
            select(MatchFeedback).where(
                MatchFeedback.match_id == match.id, MatchFeedback.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = MatchFeedback(
            tenant_id=match.tenant_id, match_id=match.id, user_id=user_id, thumb=value
        )
        session.add(row)
    row.thumb = value
    row.reason = (reason or None) and reason.strip()[:2000]
    await session.flush()
    return row


async def latest_match(
    session: AsyncSession,
    opportunity_id: uuid.UUID,
    *,
    profile_id: uuid.UUID | None = None,
) -> Match | None:
    """The newest scored match of this tenant for the notice (RLS-scoped)."""
    stmt = select(Match).where(Match.opportunity_id == opportunity_id)
    if profile_id is not None:
        stmt = stmt.where(Match.profile_id == profile_id)
    stmt = stmt.order_by(
        Match.opportunity_version.desc(), Match.profile_version.desc(), Match.created_at.desc()
    )
    return (await session.execute(stmt.limit(1))).scalars().first()


# --- weekly re-tune -------------------------------------------------------------------------


async def collect_observations(
    session: AsyncSession, profile_id: uuid.UUID, *, since: datetime
) -> list[Observation]:
    """One Observation per thumb in the window, carrying its notice's candidate terms."""
    rows = (
        await session.execute(
            select(MatchFeedback.thumb, Opportunity.title, Opportunity.summary_ai)
            .join(Match, Match.id == MatchFeedback.match_id)
            .join(Opportunity, Opportunity.id == Match.opportunity_id)
            .where(Match.profile_id == profile_id, MatchFeedback.created_at >= since)
        )
    ).all()
    existing = (
        (
            await session.execute(
                select(ProfileKeyword.term).where(
                    ProfileKeyword.profile_id == profile_id,
                    ProfileKeyword.kind == KeywordKind.INCLUDE,
                )
            )
        )
        .scalars()
        .all()
    )
    watched = [str(term) for term in existing]
    return [
        Observation(
            terms=candidate_terms(title, summary, extra=watched),
            positive=str(thumb) == Thumb.UP.value,
        )
        for thumb, title, summary in rows
    ]


async def retune_profile(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    now: datetime | None = None,
    window_days: int = FEEDBACK_WINDOW_DAYS,
    min_support: int = MIN_SUPPORT,
    min_lift: Decimal = MIN_LIFT,
) -> list[KeywordSuggestion]:
    """Refresh the profile's PENDING suggestions from the last `window_days` of thumbs.

    A term the owner already decided on (approved or rejected) is left alone; a pending
    row is updated in place so the evidence always reflects the latest window.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=window_days)
    observations = await collect_observations(session, profile_id, since=since)
    lifts = keyword_lift(observations, min_support=min_support, min_lift=min_lift)
    decided = {
        (str(kind), str(term))
        for kind, term, status in (
            await session.execute(
                select(
                    KeywordSuggestion.kind, KeywordSuggestion.term, KeywordSuggestion.status
                ).where(KeywordSuggestion.profile_id == profile_id)
            )
        ).all()
        if str(status) != SuggestionStatus.PENDING.value
    }
    pending = {
        (row.kind, row.term): row
        for row in (
            (
                await session.execute(
                    select(KeywordSuggestion).where(
                        KeywordSuggestion.profile_id == profile_id,
                        KeywordSuggestion.status == SuggestionStatus.PENDING.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    written: list[KeywordSuggestion] = []
    fresh: set[tuple[str, str]] = set()
    for lift in lifts:
        term = lift.term[:MAX_KEYWORD_CHARS]
        key = (lift.kind, term)
        if key in decided:
            continue
        fresh.add(key)
        row = pending.get(key)
        if row is None:
            row = KeywordSuggestion(
                tenant_id=tenant_id, profile_id=profile_id, term=term, kind=lift.kind
            )
            session.add(row)
        row.delta_weight = lift.delta_weight
        row.evidence = lift.evidence()
        row.status = SuggestionStatus.PENDING.value
        written.append(row)
    # a pending suggestion the newest window no longer supports is withdrawn
    for key, row in pending.items():
        if key not in fresh:
            await session.delete(row)
    await session.flush()
    log.info(
        "matching.retuned",
        profile_id=str(profile_id),
        observations=len(observations),
        suggestions=len(written),
    )
    return written


# --- applying a decision ----------------------------------------------------------------------


async def decide_suggestion(
    session: AsyncSession,
    suggestion: KeywordSuggestion,
    *,
    status: str,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> KeywordSuggestion:
    """Approve (which writes profile_keywords) or reject one suggestion."""
    decision = SuggestionStatus(status)
    if decision is SuggestionStatus.PENDING:
        raise ValueError("a decision must be approved or rejected")
    suggestion.status = decision.value
    suggestion.decided_at = now or datetime.now(UTC)
    suggestion.decided_by = user_id
    if decision is SuggestionStatus.APPROVED:
        await apply_suggestion(session, suggestion)
    await session.flush()
    return suggestion


async def apply_suggestion(session: AsyncSession, suggestion: KeywordSuggestion) -> ProfileKeyword:
    """Write the approved term into profile_keywords (create, or nudge the weight)."""
    kind = KeywordKind.INCLUDE if suggestion.kind == INCLUDE else KeywordKind.EXCLUDE
    row = (
        await session.execute(
            select(ProfileKeyword).where(
                ProfileKeyword.profile_id == suggestion.profile_id,
                ProfileKeyword.kind == kind,
                ProfileKeyword.term == suggestion.term,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        weight = Decimal("1.0")
        if kind is KeywordKind.INCLUDE:
            weight = _clamp_weight(Decimal("1.0") + Decimal(suggestion.delta_weight))
        row = ProfileKeyword(
            tenant_id=suggestion.tenant_id,
            profile_id=suggestion.profile_id,
            kind=kind,
            term=suggestion.term,
            weight=weight,
        )
        session.add(row)
    elif kind is KeywordKind.INCLUDE:
        row.weight = _clamp_weight(Decimal(row.weight) + Decimal(suggestion.delta_weight))
    await session.flush()
    return row


def _clamp_weight(value: Decimal) -> Decimal:
    return max(MIN_WEIGHT, min(MAX_WEIGHT, value.quantize(Decimal("0.1"))))


async def profiles_with_feedback(
    session: AsyncSession, *, since: datetime
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(tenant_id, profile_id) pairs that got a thumb in the window (RLS-scoped)."""
    rows = (
        await session.execute(
            select(Match.tenant_id, Match.profile_id)
            .join(MatchFeedback, MatchFeedback.match_id == Match.id)
            .where(MatchFeedback.created_at >= since)
            .distinct()
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def suggestion_out(row: KeywordSuggestion) -> dict[str, object]:
    return {
        "id": str(row.id),
        "profile_id": str(row.profile_id),
        "term": row.term,
        "kind": row.kind,
        "delta_weight": str(row.delta_weight),
        "evidence": dict(row.evidence or {}),
        "status": row.status,
        "created_at": row.created_at,
        "decided_at": row.decided_at,
    }


__all__ = [
    "EXCLUDE",
    "FEEDBACK_WINDOW_DAYS",
    "INCLUDE",
    "TermLift",
    "apply_suggestion",
    "collect_observations",
    "decide_suggestion",
    "latest_match",
    "profiles_with_feedback",
    "record_feedback",
    "retune_profile",
    "suggestion_out",
]
