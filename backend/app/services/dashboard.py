"""Dashboard KPIs (SPEC 9, 10.4 screen 2; M6-08).

    kpis = await dashboard(session, settings, user_tz="Asia/Kolkata")

Every figure is a SQL aggregate over the caller's tenant session, so RLS is the only
scoping there is: no tenant_id is ever passed by hand.

`alert_precision` reads `match_feedback`, which the parallel matching branch owns. This
branch has no such table, so the query is guarded by an information_schema check and the
KPI answers null until the branches merge (PROGRESS.m6.md OQ-135).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pursuit_stages as stages
from app.core.config import Settings
from app.core.dashboard import HOURS_SAVED_BASIS, alert_precision, hours_saved, value_pair, win_rate
from app.models import Opportunity, Pursuit

log = structlog.get_logger(__name__)

DUE_WINDOW_DAYS = 7
DUE_LIMIT = 50
FEEDBACK_TABLE = "match_feedback"
# M4-07 records a thumb per (match, user); "up" is a useful alert (OQ-135 resolved)
FEEDBACK_USEFUL_VALUES = ("up",)
TABLE_EXISTS_SQL = (
    "SELECT 1 FROM information_schema.tables "
    "WHERE table_schema = current_schema() AND table_name = :name"
)
# FEEDBACK_TABLE is a module constant, never user input (ruff S608 is about injection)
FEEDBACK_SQL = (
    "SELECT count(*) FILTER (WHERE thumb = ANY(:useful)) AS useful, count(*) AS rated "
    f"FROM {FEEDBACK_TABLE}"
)


@dataclass
class DueItem:
    pursuit_id: str
    opportunity_id: str
    title: str
    stage: str
    owner_user_id: str | None
    due_at: datetime
    buyer_tz: str


@dataclass
class Kpis:
    open_by_stage: dict[str, int] = field(default_factory=dict)
    due_next_7_days: list[DueItem] = field(default_factory=list)
    pipeline_value_by_stage: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    pipeline_value_total: dict[str, Decimal] = field(default_factory=dict)
    win_rate: float | None = None
    awarded: int = 0
    lost: int = 0
    submitted: int = 0
    hours_saved_per_package: int = 0
    hours_saved_total: int = 0
    hours_saved_basis: str = HOURS_SAVED_BASIS
    alert_precision: float | None = None
    alert_feedback_rated: int = 0


async def counts_by_stage(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(select(Pursuit.stage, func.count()).group_by(Pursuit.stage))
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


async def value_by_stage(session: AsyncSession) -> dict[str, Decimal]:
    """USD pipeline value per stage, from the notice's estimated value (max, else min)."""
    value = func.coalesce(Opportunity.estimated_value_max_usd, Opportunity.estimated_value_min_usd)
    rows = (
        await session.execute(
            select(Pursuit.stage, func.coalesce(func.sum(value), 0))
            .join(Opportunity, Opportunity.id == Pursuit.opportunity_id)
            .group_by(Pursuit.stage)
        )
    ).all()
    return {str(row[0]): Decimal(row[1] or 0) for row in rows}


async def due_soon(session: AsyncSession, *, now: datetime) -> list[DueItem]:
    """Open pursuits whose notice closes inside the next seven days, soonest first."""
    until = now + timedelta(days=DUE_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(Pursuit, Opportunity)
            .join(Opportunity, Opportunity.id == Pursuit.opportunity_id)
            .where(
                Pursuit.stage.in_(list(stages.OPEN_STAGES)),
                Opportunity.response_due_at.is_not(None),
                Opportunity.response_due_at >= now,
                Opportunity.response_due_at <= until,
            )
            .order_by(Opportunity.response_due_at)
            .limit(DUE_LIMIT)
        )
    ).all()
    return [
        DueItem(
            pursuit_id=str(pursuit.id),
            opportunity_id=str(opportunity.id),
            title=opportunity.title,
            stage=pursuit.stage,
            owner_user_id=None if pursuit.owner_user_id is None else str(pursuit.owner_user_id),
            due_at=opportunity.response_due_at,
            buyer_tz=opportunity.source_tz,
        )
        for pursuit, opportunity in rows
        if opportunity.response_due_at is not None
    ]


async def feedback_precision(session: AsyncSession) -> tuple[float | None, int]:
    """Alert precision from `match_feedback`, or (None, 0) when that table is absent.

    The matching branch owns the table; guarding on information_schema means this KPI
    lights up the day the branches merge without a code change here.
    """
    found = (await session.execute(text(TABLE_EXISTS_SQL), {"name": FEEDBACK_TABLE})).first()
    if found is None:
        return None, 0
    try:
        # a savepoint keeps a column-shape error from aborting the caller's transaction
        async with session.begin_nested():
            row = (
                await session.execute(text(FEEDBACK_SQL), {"useful": list(FEEDBACK_USEFUL_VALUES)})
            ).one()
    except (ProgrammingError, DBAPIError) as exc:  # a different column shape
        log.info("dashboard.match_feedback_unreadable", error=str(exc)[:200])
        return None, 0
    useful, rated = int(row[0] or 0), int(row[1] or 0)
    return alert_precision(useful=useful, rated=rated), rated


async def dashboard(
    session: AsyncSession, settings: Settings, *, now: datetime | None = None
) -> Kpis:
    moment = now or datetime.now(UTC)
    counts = await counts_by_stage(session)
    values = await value_by_stage(session)
    rates: dict[str, Any] = dict(settings.fx_rates)

    open_by_stage = {stage: counts.get(stage, 0) for stage in stages.OPEN_STAGES}
    pipeline = {
        stage: value_pair(values.get(stage), rates)
        for stage in stages.OPEN_STAGES
        if counts.get(stage, 0)
    }
    total_usd = sum((values.get(stage, Decimal(0)) for stage in stages.OPEN_STAGES), Decimal(0))
    awarded = counts.get(stages.STAGE_AWARDED, 0)
    lost = counts.get(stages.STAGE_LOST, 0)
    submitted = counts.get(stages.STAGE_SUBMITTED, 0) + awarded + lost
    precision, rated = await feedback_precision(session)
    return Kpis(
        open_by_stage=open_by_stage,
        due_next_7_days=await due_soon(session, now=moment),
        pipeline_value_by_stage=pipeline,
        pipeline_value_total=value_pair(total_usd, rates),
        win_rate=win_rate(awarded=awarded, lost=lost),
        awarded=awarded,
        lost=lost,
        submitted=submitted,
        hours_saved_per_package=settings.hours_saved_per_package,
        hours_saved_total=hours_saved(
            submitted=submitted, per_package=settings.hours_saved_per_package
        ),
        alert_precision=precision,
        alert_feedback_rated=rated,
    )
