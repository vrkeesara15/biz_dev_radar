"""Dedupe inside the ingest pipeline (SPEC 5.4, M2-10).

Runs after an opportunity was created or changed:

1. cross-source key: same `reference_norm` (normalised solicitation/tender reference) and
   same `buyer_norm`, from a DIFFERENT source (same-source repeats are amendments and are
   linked through parent_opportunity_id instead);
2. fuzzy fallback: pg_trgm similarity(title) >= 0.9, same buyer_norm, response_due_at
   within one day, different source.

The richer record (core.dedupe.richness) survives; the other gets `duplicate_of` and the
survivor's `extra.also_from` keeps the loser's source link. A record that is already a
duplicate is left alone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dedupe import (
    CROSS_SOURCE_KEY,
    DUE_WINDOW_SECONDS,
    FUZZY_TITLE,
    RICHNESS_FIELDS,
    TITLE_SIMILARITY,
    Candidate,
    also_from_entries,
    pick_winner,
    richness,
)
from app.models import Opportunity, OpportunityDocument

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    id: uuid.UUID
    method: str


@dataclass(frozen=True, slots=True)
class Merge:
    winner_id: uuid.UUID
    loser_id: uuid.UUID
    method: str


async def find_duplicates(session: AsyncSession, row: Opportunity) -> list[DuplicateCandidate]:
    """Candidates from other sources that look like the same notice (key first, then fuzzy)."""
    found: list[DuplicateCandidate] = []
    seen: set[uuid.UUID] = set()
    if row.id is not None:
        seen.add(row.id)
    if row.buyer_norm is None:
        return found

    if row.reference_norm:
        stmt = (
            select(Opportunity.id)
            .where(
                Opportunity.reference_norm == row.reference_norm,
                Opportunity.buyer_norm == row.buyer_norm,
                Opportunity.source_id != row.source_id,
            )
            .order_by(Opportunity.created_at.asc())
        )
        for (candidate_id,) in (await session.execute(stmt)).all():
            if candidate_id not in seen:
                seen.add(candidate_id)
                found.append(DuplicateCandidate(candidate_id, CROSS_SOURCE_KEY))

    if row.response_due_at is not None and row.title:
        window = timedelta(seconds=DUE_WINDOW_SECONDS)
        stmt = (
            select(Opportunity.id)
            .where(
                Opportunity.buyer_norm == row.buyer_norm,
                Opportunity.source_id != row.source_id,
                Opportunity.response_due_at.between(
                    row.response_due_at - window, row.response_due_at + window
                ),
                func.similarity(Opportunity.title, row.title) >= TITLE_SIMILARITY,
            )
            .order_by(Opportunity.created_at.asc())
        )
        for (candidate_id,) in (await session.execute(stmt)).all():
            if candidate_id not in seen:
                seen.add(candidate_id)
                found.append(DuplicateCandidate(candidate_id, FUZZY_TITLE))
    return found


async def _document_count(session: AsyncSession, opportunity_id: uuid.UUID) -> int:
    count: int = (
        await session.execute(
            select(func.count())
            .select_from(OpportunityDocument)
            .where(OpportunityDocument.opportunity_id == opportunity_id)
        )
    ).scalar_one()
    return count


async def row_richness(session: AsyncSession, row: Opportunity) -> int:
    values: dict[str, Any] = {name: getattr(row, name, None) for name in RICHNESS_FIELDS}
    return richness(values, document_count=await _document_count(session, row.id))


def _link(row: Opportunity) -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "external_id": row.external_id,
        "source_url": row.source_url,
    }


async def _merge(session: AsyncSession, winner: Opportunity, loser: Opportunity) -> None:
    loser.duplicate_of = winner.id
    loser_links = [_link(loser), *((loser.extra or {}).get("also_from") or [])]
    winner.extra = {
        **(winner.extra or {}),
        "also_from": also_from_entries((winner.extra or {}).get("also_from"), loser_links),
    }
    # anything that pointed at the loser now points at the survivor
    await session.execute(
        update(Opportunity)
        .where(Opportunity.duplicate_of == loser.id)
        .values(duplicate_of=winner.id)
    )
    await session.flush()


async def dedupe(session: AsyncSession, row: Opportunity) -> list[Merge]:
    """Merge `row` with its duplicates; returns the merges made (possibly none)."""
    if row.duplicate_of is not None:
        return []
    merges: list[Merge] = []
    for cand in await find_duplicates(session, row):
        other = await session.get(Opportunity, cand.id)
        if other is None:
            continue
        if other.duplicate_of is not None:
            root = await session.get(Opportunity, other.duplicate_of)
            if root is None or root.id == row.id:
                continue
            other = root
        pair = [
            Candidate(row.id, await row_richness(session, row), row.created_at),
            Candidate(other.id, await row_richness(session, other), other.created_at),
        ]
        winner_id = pick_winner(pair).id
        winner, loser = (row, other) if winner_id == row.id else (other, row)
        await _merge(session, winner, loser)
        merges.append(Merge(winner_id=winner.id, loser_id=loser.id, method=cand.method))
        log.info(
            "opportunity.deduped",
            winner=str(winner.id),
            loser=str(loser.id),
            method=cand.method,
        )
        if loser is row:
            break
    return merges
