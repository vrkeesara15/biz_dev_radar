"""Saved searches and alert rules (M4-08, SPEC 10.3).

    GET/POST  /api/v1/saved-searches            (POST also creates the rule)
    GET/POST  /api/v1/alert-rules
    PATCH     /api/v1/alert-rules/{id}

A saved search belongs to the user who made it; alert rules are tenant-wide (a rule may
name a user, which is who its instant alerts go to). Every tenant role may save a search
for themselves; only an owner or bid manager may create or edit a bare alert rule.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.core.matching.saved_search import SearchFilters
from app.core.preferences import DEFAULT_MIN_SCORE_INSTANT
from app.core.roles import Role
from app.models import AlertMode, AlertRule, CompanyProfile, SavedSearch
from app.notify.registry import CHANNEL_NAMES, normalize_channels
from app.services.audit import AuditHint
from app.services.matching.alerts import DEFAULT_CHANNELS, create_saved_search

searches_router = APIRouter(prefix="/saved-searches", tags=["alerts"])
rules_router = APIRouter(prefix="/alert-rules", tags=["alerts"])

ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]
RuleEditorDep = Annotated[CurrentUser, Depends(require_role(Role.TENANT_OWNER, Role.BID_MANAGER))]

Mode = Literal["instant", "digest"]


def _channels(value: Any) -> list[str]:
    channels = normalize_channels(value)
    if value and not channels:
        raise ValueError(f"no usable channel in {value!r}; one of {list(CHANNEL_NAMES)}")
    return list(channels)


class SavedSearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filters: dict[str, Any] = Field(default_factory=dict)
    # the rule created alongside it
    min_score: int | None = Field(default=None, ge=0, le=100)
    channels: list[str] = Field(default_factory=lambda: list(DEFAULT_CHANNELS))
    mode: Mode = "instant"
    profile_id: uuid.UUID | None = None

    @field_validator("channels")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _channels(value)


class SavedSearchOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    filters: dict[str, Any]
    created_at: datetime
    alert_rule_id: uuid.UUID | None = None


class AlertRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    min_score: int = Field(default=DEFAULT_MIN_SCORE_INSTANT, ge=0, le=100)
    channels: list[str] = Field(default_factory=lambda: list(DEFAULT_CHANNELS))
    mode: Mode = "instant"
    enabled: bool = True
    saved_search_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    @field_validator("channels")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _channels(value)


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    min_score: int | None = Field(default=None, ge=0, le=100)
    channels: list[str] | None = None
    mode: Mode | None = None
    enabled: bool | None = None

    @field_validator("channels")
    @classmethod
    def _clean(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _channels(value)


class AlertRuleOut(BaseModel):
    id: uuid.UUID
    name: str
    min_score: int
    channels: list[str]
    mode: Mode
    enabled: bool
    saved_search_id: uuid.UUID | None
    profile_id: uuid.UUID | None
    user_id: uuid.UUID | None
    created_at: datetime


def _search_out(row: SavedSearch, rule_id: uuid.UUID | None = None) -> SavedSearchOut:
    return SavedSearchOut(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        filters=dict(row.filters or {}),
        created_at=row.created_at,
        alert_rule_id=rule_id,
    )


def _rule_out(row: AlertRule) -> AlertRuleOut:
    return AlertRuleOut.model_validate(row, from_attributes=True)


async def _check_profile(session: TenantSessionDep, profile_id: uuid.UUID | None) -> None:
    if profile_id is None:
        return
    if await session.get(CompanyProfile, profile_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")


# --- saved searches -------------------------------------------------------------------------


@searches_router.get("", response_model=list[SavedSearchOut])
async def list_saved_searches(session: TenantSessionDep, user: ReaderDep) -> list[SavedSearchOut]:
    """The caller's own saved searches, oldest first, each with its alert rule id."""
    rows = (
        (
            await session.execute(
                select(SavedSearch)
                .where(SavedSearch.user_id == user.id)
                .order_by(SavedSearch.created_at, SavedSearch.id)
            )
        )
        .scalars()
        .all()
    )
    rules = {
        rule.saved_search_id: rule.id
        for rule in (
            (
                await session.execute(
                    select(AlertRule).where(
                        AlertRule.saved_search_id.in_([r.id for r in rows] or [uuid.uuid4()])
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    return [_search_out(row, rules.get(row.id)) for row in rows]


@searches_router.post("", response_model=SavedSearchOut, status_code=status.HTTP_201_CREATED)
async def post_saved_search(
    body: SavedSearchIn, session: TenantSessionDep, user: ReaderDep, request: Request
) -> SavedSearchOut:
    await _check_profile(session, body.profile_id)
    try:
        search, rule = await create_saved_search(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            name=body.name,
            filters=body.filters,
            channels=body.channels,
            mode=body.mode,
            min_score=body.min_score,
            profile_id=body.profile_id,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="a saved search or rule with that name exists"
        ) from exc
    request.state.audit = AuditHint(
        action="saved_search.create",
        object_type="saved_search",
        object_id=str(search.id),
        meta={
            "alert_rule_id": str(rule.id),
            "filters": SearchFilters.from_dict(body.filters).as_dict(),
        },
    )
    return _search_out(search, rule.id)


# --- alert rules ----------------------------------------------------------------------------


@rules_router.get("", response_model=list[AlertRuleOut])
async def list_alert_rules(session: TenantSessionDep, _user: ReaderDep) -> list[AlertRuleOut]:
    rows = (
        (await session.execute(select(AlertRule).order_by(AlertRule.created_at, AlertRule.id)))
        .scalars()
        .all()
    )
    return [_rule_out(row) for row in rows]


@rules_router.post("", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def post_alert_rule(
    body: AlertRuleIn, session: TenantSessionDep, user: RuleEditorDep, request: Request
) -> AlertRuleOut:
    await _check_profile(session, body.profile_id)
    if (
        body.saved_search_id is not None
        and await session.get(SavedSearch, body.saved_search_id) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="saved search not found")
    row = AlertRule(
        tenant_id=user.tenant_id,
        saved_search_id=body.saved_search_id,
        profile_id=body.profile_id,
        user_id=body.user_id or user.id,
        name=body.name.strip(),
        min_score=body.min_score,
        channels=body.channels or list(DEFAULT_CHANNELS),
        mode=AlertMode(body.mode).value,
        enabled=body.enabled,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="an alert rule with that name exists"
        ) from exc
    request.state.audit = AuditHint(
        action="alert_rule.create", object_type="alert_rule", object_id=str(row.id)
    )
    return _rule_out(row)


@rules_router.patch("/{rule_id}", response_model=AlertRuleOut)
async def patch_alert_rule(
    rule_id: uuid.UUID,
    body: AlertRuleUpdate,
    session: TenantSessionDep,
    user: RuleEditorDep,
    request: Request,
) -> AlertRuleOut:
    row = await session.get(AlertRule, rule_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="alert rule not found")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(row, key, AlertMode(value).value if key == "mode" else value)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="an alert rule with that name exists"
        ) from exc
    request.state.audit = AuditHint(
        action="alert_rule.update",
        object_type="alert_rule",
        object_id=str(row.id),
        meta={"fields": sorted(changes)},
    )
    return _rule_out(row)
