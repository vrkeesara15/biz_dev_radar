"""Saved searches and alert rules (M4-08, SPEC 6 / 7 / 10.3).

    search = await create_saved_search(session, tenant_id, user_id, name=..., filters=...)
    decisions = await evaluate_rules(session, opportunity=row, profile_id=..., score=72,
                                     band="high", now=now)

Every new match is evaluated against every ENABLED alert rule of its tenant: a rule fires
when its `min_score` is met, its `profile_id` (when set) is the profile that was scored,
and its saved search's filters accept the notice. The decisions say which channels and
which timing (instant or digest); the event router (M4-14) turns them into notifications
and falls back to the SPEC 7 defaults when no rule fired.

Creating a saved search creates an alert rule for it (SPEC 6: "each saved search is also
an alert rule"), instant at the saved search's own `min_score` or the SPEC 7 High
threshold, on the channels the notification defaults use.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.matching.saved_search import SearchFilters
from app.core.preferences import DEFAULT_MIN_SCORE_DIGEST, DEFAULT_MIN_SCORE_INSTANT
from app.models import AlertMode, AlertRule, MatchBand, Opportunity, SavedSearch
from app.notify.registry import normalize_channels
from app.services.matching.loaders import match_opportunity_from_row

log = structlog.get_logger(__name__)

DEFAULT_CHANNELS: tuple[str, ...] = ("in_app", "email")
MAX_NAME_CHARS = 120


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """One enabled rule that accepted this match."""

    rule_id: uuid.UUID
    name: str
    channels: tuple[str, ...]
    mode: str  # instant | digest
    min_score: int
    saved_search_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    @property
    def instant(self) -> bool:
        return self.mode == AlertMode.INSTANT.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": str(self.rule_id),
            "name": self.name,
            "channels": list(self.channels),
            "mode": self.mode,
            "min_score": self.min_score,
            "saved_search_id": None if self.saved_search_id is None else str(self.saved_search_id),
            "user_id": None if self.user_id is None else str(self.user_id),
        }


def default_min_score(band: str) -> int:
    """SPEC 7: High >= 70 is instant, Medium 50-69 goes to the digest."""
    return DEFAULT_MIN_SCORE_INSTANT if band == MatchBand.HIGH.value else DEFAULT_MIN_SCORE_DIGEST


# --- saved searches --------------------------------------------------------------------------


async def create_saved_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    filters: dict[str, Any] | None,
    channels: Sequence[str] | None = None,
    mode: str = AlertMode.INSTANT.value,
    min_score: int | None = None,
    profile_id: uuid.UUID | None = None,
) -> tuple[SavedSearch, AlertRule]:
    """Store the filter set AND its alert rule in one go (SPEC 6)."""
    parsed = SearchFilters.from_dict(filters)
    search = SavedSearch(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name.strip()[:MAX_NAME_CHARS],
        filters=parsed.as_dict(),
    )
    session.add(search)
    await session.flush()
    rule = AlertRule(
        tenant_id=tenant_id,
        saved_search_id=search.id,
        profile_id=profile_id,
        user_id=user_id,
        name=_unique_rule_name(search.name),
        min_score=(
            min_score
            if min_score is not None
            else (parsed.min_score if parsed.min_score is not None else DEFAULT_MIN_SCORE_INSTANT)
        ),
        channels=list(normalize_channels(channels) or DEFAULT_CHANNELS),
        mode=AlertMode(mode).value,
        enabled=True,
    )
    session.add(rule)
    await session.flush()
    return search, rule


def _unique_rule_name(name: str) -> str:
    return name[:MAX_NAME_CHARS]


# --- evaluation ------------------------------------------------------------------------------


async def enabled_rules(session: AsyncSession) -> list[AlertRule]:
    return list(
        (
            await session.execute(
                select(AlertRule)
                .where(AlertRule.enabled.is_(True))
                .order_by(AlertRule.created_at, AlertRule.id)
            )
        )
        .scalars()
        .all()
    )


async def _filters_for(
    session: AsyncSession, rules: Sequence[AlertRule]
) -> dict[uuid.UUID, SearchFilters]:
    ids = [r.saved_search_id for r in rules if r.saved_search_id is not None]
    if not ids:
        return {}
    rows = (
        (await session.execute(select(SavedSearch).where(SavedSearch.id.in_(ids)))).scalars().all()
    )
    return {row.id: SearchFilters.from_dict(row.filters) for row in rows}


async def evaluate_rules(
    session: AsyncSession,
    *,
    opportunity: Opportunity,
    profile_id: uuid.UUID,
    score: Decimal | int | float,
    now: datetime | None = None,
) -> list[AlertDecision]:
    """Every enabled rule of the caller's tenant that accepts this match (RLS-scoped)."""
    rules = await enabled_rules(session)
    if not rules:
        return []
    moment = now or datetime.now(UTC)
    opp = match_opportunity_from_row(opportunity)
    filters = await _filters_for(session, rules)
    value = Decimal(str(score))
    out: list[AlertDecision] = []
    for rule in rules:
        if rule.profile_id is not None and rule.profile_id != profile_id:
            continue
        if value < Decimal(rule.min_score):
            continue
        if rule.saved_search_id is not None:
            saved = filters.get(rule.saved_search_id)
            if saved is None or not saved.matches(opp, now=moment, score=value):
                continue
        channels = normalize_channels(rule.channels) or DEFAULT_CHANNELS
        out.append(
            AlertDecision(
                rule_id=rule.id,
                name=rule.name,
                channels=channels,
                mode=rule.mode,
                min_score=rule.min_score,
                saved_search_id=rule.saved_search_id,
                user_id=rule.user_id,
            )
        )
    log.debug(
        "alerts.evaluated",
        opportunity_id=str(opportunity.id),
        rules=len(rules),
        fired=len(out),
    )
    return out


__all__ = [
    "DEFAULT_CHANNELS",
    "AlertDecision",
    "create_saved_search",
    "default_min_score",
    "enabled_rules",
    "evaluate_rules",
]
