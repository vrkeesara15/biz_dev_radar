"""Keyword re-tune suggestions (M4-07, SPEC 6 learning loop / 10.3).

    GET /api/v1/profiles/{profile_id}/keyword-suggestions?status=pending
    PUT /api/v1/profiles/{profile_id}/keyword-suggestions/{id}   {"status": "approved"}

The weekly job proposes; only an owner or bid manager decides. Approving writes
`profile_keywords` (a new term, or a nudge to an existing include weight); rejecting
stamps the row so the job never proposes that term again.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import TenantSessionDep
from app.api.v1.profiles.common import EditorDep, ReaderDep, get_profile
from app.models import KeywordSuggestion, SuggestionStatus
from app.services.audit import AuditHint
from app.services.matching.learning import decide_suggestion

router = APIRouter(prefix="/{profile_id}/keyword-suggestions", tags=["profiles:keywords"])


class SuggestionOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    term: str
    kind: Literal["include", "exclude"]
    delta_weight: Decimal
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    decided_at: datetime | None = None


class SuggestionDecision(BaseModel):
    status: Literal["approved", "rejected"]


def _out(row: KeywordSuggestion) -> SuggestionOut:
    return SuggestionOut.model_validate(row, from_attributes=True)


@router.get("", response_model=list[SuggestionOut], name="list_keyword_suggestions")
async def list_suggestions(
    profile_id: uuid.UUID,
    session: TenantSessionDep,
    _user: ReaderDep,
    status_filter: Annotated[
        str | None, Query(alias="status", description="pending | approved | rejected")
    ] = None,
) -> list[SuggestionOut]:
    profile = await get_profile(session, profile_id)
    stmt = select(KeywordSuggestion).where(KeywordSuggestion.profile_id == profile.id)
    if status_filter is not None:
        try:
            stmt = stmt.where(KeywordSuggestion.status == SuggestionStatus(status_filter).value)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"unknown status {status_filter!r}"
            ) from exc
    rows = (
        (await session.execute(stmt.order_by(KeywordSuggestion.created_at, KeywordSuggestion.id)))
        .scalars()
        .all()
    )
    return [_out(row) for row in rows]


# The path parameter is `id`, not `suggestion_id`: audit_log.action is
# "<method> <route template>" in varchar(64), and the longer name overflows it.
@router.put("/{id}", response_model=SuggestionOut, name="decide_keyword_suggestion")
async def decide(
    profile_id: uuid.UUID,
    id: uuid.UUID,
    body: SuggestionDecision,
    session: TenantSessionDep,
    user: EditorDep,
    request: Request,
) -> SuggestionOut:
    profile = await get_profile(session, profile_id)
    row = await session.get(KeywordSuggestion, id)
    if row is None or row.profile_id != profile.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="keyword suggestion not found")
    if row.status != SuggestionStatus.PENDING.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"suggestion already {row.status}")
    await decide_suggestion(session, row, status=body.status, user_id=user.id)
    request.state.audit = AuditHint(
        action=f"profile.keyword_suggestion.{body.status}",
        object_type="keyword_suggestion",
        object_id=str(row.id),
        meta={"profile_id": str(profile.id), "term": row.term, "kind": row.kind},
    )
    return _out(row)
