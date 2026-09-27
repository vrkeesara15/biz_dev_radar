"""Dashboard KPIs (SPEC 9, 10.4 screen 2; M6-08).

    GET /api/v1/dashboard

Open pursuits by stage, what is due in the next seven days (with both time zones),
pipeline value per stage in USD and INR, win rate, the hours-saved estimate and alert
precision. Everything is a SQL aggregate over the caller's tenant session, so RLS does
the scoping; every tenant role may read it (SPEC 3 "dashboard.view").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import TENANT_ROLES, CurrentUser, SettingsDep, TenantSessionDep, require_role
from app.core.display_time import TzDateOut, countdown, tz_fields
from app.models import User
from app.services import dashboard as dashboard_svc

router = APIRouter(tags=["dashboard"])
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]


class DueSoonOut(BaseModel):
    pursuit_id: uuid.UUID
    opportunity_id: uuid.UUID
    title: str
    stage: str
    owner_user_id: uuid.UUID | None
    due_at: TzDateOut
    countdown: str


class MoneyPairOut(BaseModel):
    USD: Decimal
    INR: Decimal


class DashboardOut(BaseModel):
    """SPEC 9: "open pursuits by stage, due in next 7 days, pipeline value by stage (USD
    and INR), win rate, average hours saved per package, alert precision from feedback"."""

    generated_at: datetime
    open_by_stage: dict[str, int]
    due_next_7_days: list[DueSoonOut]
    pipeline_value_by_stage: dict[str, MoneyPairOut]
    pipeline_value_total: MoneyPairOut
    win_rate: float | None
    awarded: int
    lost: int
    submitted: int
    avg_hours_saved_per_package: int
    hours_saved_total: int
    hours_saved_basis: str
    # null until the matching branch's match_feedback table exists (OQ-135)
    alert_precision: float | None
    alert_feedback_rated: int


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    session: TenantSessionDep, user: ReaderDep, settings: SettingsDep
) -> DashboardOut:
    reader = await session.get(User, user.id)
    user_tz = None if reader is None else reader.tz
    kpis = await dashboard_svc.dashboard(session, settings)
    now = datetime.now(UTC)
    return DashboardOut(
        generated_at=now,
        open_by_stage=kpis.open_by_stage,
        due_next_7_days=[
            DueSoonOut(
                pursuit_id=uuid.UUID(item.pursuit_id),
                opportunity_id=uuid.UUID(item.opportunity_id),
                title=item.title,
                stage=item.stage,
                owner_user_id=None if item.owner_user_id is None else uuid.UUID(item.owner_user_id),
                due_at=tz_fields(item.due_at, item.buyer_tz, user_tz, with_year=True),
                countdown=countdown(now, item.due_at),
            )
            for item in kpis.due_next_7_days
        ],
        pipeline_value_by_stage={
            stage: MoneyPairOut(USD=pair["USD"], INR=pair["INR"])
            for stage, pair in kpis.pipeline_value_by_stage.items()
        },
        pipeline_value_total=MoneyPairOut(
            USD=kpis.pipeline_value_total["USD"], INR=kpis.pipeline_value_total["INR"]
        ),
        win_rate=kpis.win_rate,
        awarded=kpis.awarded,
        lost=kpis.lost,
        submitted=kpis.submitted,
        avg_hours_saved_per_package=kpis.hours_saved_per_package,
        hours_saved_total=kpis.hours_saved_total,
        hours_saved_basis=kpis.hours_saved_basis,
        alert_precision=kpis.alert_precision,
        alert_feedback_rated=kpis.alert_feedback_rated,
    )
