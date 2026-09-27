"""Pursuit workspace API (SPEC 10.3, 10.4): draft sections, approvals and comments.

    GET  /api/v1/pursuits/{id}/drafts                      list + grounding counts
    GET  /api/v1/pursuits/{id}/drafts/{section_id}         one section's current version
    PUT  /api/v1/pursuits/{id}/drafts/{section_id}         optimistic save (base_version)
    POST /api/v1/pursuits/{id}/drafts/{section_id}/approve reviewer / bid manager / owner
    GET  /api/v1/pursuits/{id}/comments                    comments on drafts, matrix rows
    POST /api/v1/pursuits/{id}/comments                    and the scorecard
    POST /api/v1/pursuits/{id}/comments/{comment_id}/resolve

Roles (SPEC 3): a writer edits drafts, a reviewer may only comment and approve sections,
a viewer sees dashboards but not drafts. Every draft READ is written to audit_log
(action draft.read / draft.list): unreleased bid strategy is the most sensitive content
in the product (SPEC 11).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSessionDep, require_role
from app.core.markdown import markdown_to_html
from app.core.roles import Role
from app.models import Comment, ComplianceItem, Draft, DraftVersion, Pursuit, PursuitArtifact
from app.models.drafts import (
    AUTHOR_USER,
    COMMENT_COMPLIANCE_ITEM,
    COMMENT_DRAFT,
    COMMENT_SCORECARD,
    COMMENT_TARGETS,
    DRAFT_STATUS_APPROVED,
)
from app.services import drafts as draft_svc
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint, audit
from app.services.users import ensure_user_membership

router = APIRouter(prefix="/pursuits", tags=["drafts"])

# SPEC 3: writer/SME edits drafts; reviewer comments and approves sections only; the
# viewer role is read-only dashboards and never sees draft text (core.roles draft.view).
READ_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER)
WRITE_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER)
APPROVE_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.REVIEWER)

ReaderDep = Annotated[CurrentUser, Depends(require_role(*READ_ROLES))]
WriterDep = Annotated[CurrentUser, Depends(require_role(*WRITE_ROLES))]
ApproverDep = Annotated[CurrentUser, Depends(require_role(*APPROVE_ROLES))]

AUDIT_DRAFT_READ = "draft.read"
AUDIT_DRAFT_LIST = "draft.list"


class DraftVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    body_html: str
    body_text: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    needs_input: list[dict[str, Any]] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)
    author: str
    author_user_id: uuid.UUID | None
    model: str | None
    tokens: int
    created_at: datetime


class DraftOut(BaseModel):
    pursuit_id: uuid.UUID
    id: uuid.UUID
    section_id: str
    title: str
    volume: str | None
    status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    updated_at: datetime
    current: DraftVersionOut | None
    versions: list[int] = Field(default_factory=list)
    comments: int = 0


class DraftSummaryOut(BaseModel):
    id: uuid.UUID
    section_id: str
    title: str
    volume: str | None
    status: str
    version: int | None
    unsupported_claims: int = 0
    needs_input: int = 0
    citations: int = 0
    comments: int = 0
    updated_at: datetime


class DraftListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[DraftSummaryOut] = Field(default_factory=list)
    count: int = 0
    approved: int = 0
    in_review: int = 0
    unsupported_claims_count: int = 0
    needs_input_count: int = 0
    flagged_sections: int = 0


class DraftPutIn(BaseModel):
    """Optimistic versioning: `base_version` is the version the editor started from
    (0 for a section that has no draft yet). A newer version answers 409."""

    body_html: str | None = Field(default=None, max_length=200_000)
    body_markdown: str | None = Field(default=None, max_length=200_000)
    base_version: int = Field(ge=0)
    title: str | None = Field(default=None, max_length=200)
    volume: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _one_body(self) -> DraftPutIn:
        if (self.body_html is None) == (self.body_markdown is None):
            raise ValueError("send exactly one of body_html or body_markdown")
        return self

    def html(self) -> str:
        return (
            self.body_html
            if self.body_html is not None
            else markdown_to_html(self.body_markdown or "")
        )


class CommentIn(BaseModel):
    target_type: str = Field(max_length=16)
    target_id: uuid.UUID
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("target_type")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in COMMENT_TARGETS:
            raise ValueError(f"target_type must be one of {list(COMMENT_TARGETS)}")
        return value


class CommentOut(BaseModel):
    id: uuid.UUID
    pursuit_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    body: str
    author_user_id: uuid.UUID | None
    resolved_at: datetime | None
    created_at: datetime


class CommentListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[CommentOut] = Field(default_factory=list)
    open_count: int = 0


# --- helpers -------------------------------------------------------------------------


def version_out(row: DraftVersion) -> DraftVersionOut:
    return DraftVersionOut(
        id=row.id,
        version=row.version,
        body_html=row.body_html,
        body_text=row.body_text,
        citations=list(row.citations or []),
        needs_input=list(row.needs_input or []),
        flags=dict(row.flags or {}),
        author=row.author,
        author_user_id=row.author_user_id,
        model=row.model,
        tokens=row.tokens,
        created_at=row.created_at,
    )


async def _comment_counts(session: AsyncSession, pursuit_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = (
        await session.execute(
            select(Comment.target_id).where(
                Comment.pursuit_id == pursuit_id,
                Comment.target_type == COMMENT_DRAFT,
                Comment.resolved_at.is_(None),
            )
        )
    ).scalars()
    counts: dict[uuid.UUID, int] = {}
    for target_id in rows:
        counts[target_id] = counts.get(target_id, 0) + 1
    return counts


async def _load_draft(session: AsyncSession, pursuit: Pursuit, section_id: str) -> Draft:
    draft = await draft_svc.get_draft(session, pursuit.id, section_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft section not found")
    return draft


async def draft_out(session: AsyncSession, pursuit: Pursuit, draft: Draft) -> DraftOut:
    current = await draft_svc.get_version(session, draft)
    history = await draft_svc.versions(session, draft.id)
    comments = (await _comment_counts(session, pursuit.id)).get(draft.id, 0)
    return DraftOut(
        pursuit_id=pursuit.id,
        id=draft.id,
        section_id=draft.section_id,
        title=draft.title,
        volume=draft.volume,
        status=draft.status,
        approved_by=draft.approved_by,
        approved_at=draft.approved_at,
        updated_at=draft.updated_at,
        current=None if current is None else version_out(current),
        versions=[row.version for row in history],
        comments=comments,
    )


# --- routes --------------------------------------------------------------------------


@router.get("/{pursuit_id}/drafts", response_model=DraftListOut)
async def list_drafts(
    pursuit_id: uuid.UUID, session: TenantSessionDep, user: ReaderDep, request: Request
) -> DraftListOut:
    """Every section of the pursuit with its status and grounding counts."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    rows = (
        await session.execute(
            select(Draft, DraftVersion)
            .outerjoin(DraftVersion, DraftVersion.id == Draft.current_version_id)
            .where(Draft.pursuit_id == pursuit.id)
            .order_by(Draft.volume, Draft.section_id)
        )
    ).all()
    comments = await _comment_counts(session, pursuit.id)
    items: list[DraftSummaryOut] = []
    for row in rows:
        draft = row[0]
        version: DraftVersion | None = row[1]
        flags = dict((version.flags if version else None) or {})
        items.append(
            DraftSummaryOut(
                id=draft.id,
                section_id=draft.section_id,
                title=draft.title,
                volume=draft.volume,
                status=draft.status,
                version=None if version is None else version.version,
                unsupported_claims=int(flags.get("unsupported_count") or 0),
                needs_input=len((version.needs_input if version else None) or []),
                citations=len((version.citations if version else None) or []),
                comments=comments.get(draft.id, 0),
                updated_at=draft.updated_at,
            )
        )
    summary = await draft_svc.summarise(session, pursuit.id)
    await audit(
        session,
        AUDIT_DRAFT_LIST,
        pursuit,
        user_id=user.id,
        meta={"sections": len(items)},
    )
    await session.commit()
    return DraftListOut(
        pursuit_id=pursuit.id,
        items=items,
        count=summary.count,
        approved=summary.approved,
        in_review=summary.in_review,
        unsupported_claims_count=summary.unsupported_claims_count,
        needs_input_count=summary.needs_input_count,
        flagged_sections=summary.flagged_sections,
    )


@router.get("/{pursuit_id}/drafts/{section_id}", response_model=DraftOut)
async def get_draft(
    pursuit_id: uuid.UUID,
    section_id: str,
    session: TenantSessionDep,
    user: ReaderDep,
    request: Request,
) -> DraftOut:
    """One section's current version with its citations, placeholders and flags."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    draft = await _load_draft(session, pursuit, section_id)
    out = await draft_out(session, pursuit, draft)
    await audit(
        session,
        AUDIT_DRAFT_READ,
        draft,
        user_id=user.id,
        meta={
            "pursuit_id": str(pursuit.id),
            "section_id": section_id,
            "version": None if out.current is None else out.current.version,
        },
    )
    await session.commit()
    return out


@router.put("/{pursuit_id}/drafts/{section_id}", response_model=DraftOut)
async def put_draft(
    pursuit_id: uuid.UUID,
    section_id: str,
    body: DraftPutIn,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> DraftOut:
    """Save an edit as a new version (SPEC 8: human edits are kept, never overwritten).

    `base_version` must be the version the editor loaded; anything else is 409 so two
    writers cannot silently overwrite each other. The body is sanitised and re-checked
    by the grounding validator on the way in (services.drafts.save_version).
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    existing = await draft_svc.get_draft(session, pursuit.id, section_id)
    current = 0 if existing is None else await draft_svc.latest_version_number(session, existing.id)
    if body.base_version != current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"draft {section_id} is at version {current}, not {body.base_version}; "
                "reload the section and reapply your edit"
            ),
        )
    if existing is None and not body.title:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a new draft section needs a title",
        )
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    draft, version = await draft_svc.save_version(
        session,
        user.tenant_id,
        pursuit.id,
        section_id,
        title=body.title or (existing.title if existing else section_id),
        volume=body.volume if body.volume is not None else (existing.volume if existing else None),
        body_html=body.html(),
        author=AUTHOR_USER,
        author_user_id=user.id,
    )
    request.state.audit = AuditHint(
        action="draft.saved",
        object_type="draft",
        object_id=str(draft.id),
        meta={
            "pursuit_id": str(pursuit.id),
            "section_id": section_id,
            "version": version.version,
            "unsupported_claims": int((version.flags or {}).get("unsupported_count") or 0),
        },
    )
    await session.flush()
    await session.refresh(draft)
    return await draft_out(session, pursuit, draft)


@router.post("/{pursuit_id}/drafts/{section_id}/approve", response_model=DraftOut)
async def approve_draft(
    pursuit_id: uuid.UUID,
    section_id: str,
    session: TenantSessionDep,
    user: ApproverDep,
    request: Request,
) -> DraftOut:
    """Approve one section (SPEC 3: reviewers may approve sections and nothing else)."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    draft = await _load_draft(session, pursuit, section_id)
    if draft.current_version_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="the section has no version to approve"
        )
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    draft.status = DRAFT_STATUS_APPROVED
    draft.approved_by = user.id
    draft.approved_at = datetime.now(UTC)
    request.state.audit = AuditHint(
        action="draft.approved",
        object_type="draft",
        object_id=str(draft.id),
        meta={"pursuit_id": str(pursuit.id), "section_id": section_id},
    )
    await session.flush()
    await session.refresh(draft)
    return await draft_out(session, pursuit, draft)


async def _check_target(
    session: AsyncSession, pursuit: Pursuit, target_type: str, target_id: uuid.UUID
) -> None:
    """A comment may only point at something inside this pursuit."""
    model: Any = {
        COMMENT_DRAFT: Draft,
        COMMENT_COMPLIANCE_ITEM: ComplianceItem,
        COMMENT_SCORECARD: PursuitArtifact,
    }[target_type]
    row = (
        await session.execute(
            select(model).where(model.id == target_id, model.pursuit_id == pursuit.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"{target_type} {target_id} not found in this pursuit"
        )


@router.get("/{pursuit_id}/comments", response_model=CommentListOut)
async def list_comments(
    pursuit_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    resolved: bool | None = None,
) -> CommentListOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    stmt = select(Comment).where(Comment.pursuit_id == pursuit.id)
    if target_type is not None:
        if target_type not in COMMENT_TARGETS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"target_type must be one of {list(COMMENT_TARGETS)}",
            )
        stmt = stmt.where(Comment.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(Comment.target_id == target_id)
    if resolved is not None:
        stmt = stmt.where(
            Comment.resolved_at.isnot(None) if resolved else Comment.resolved_at.is_(None)
        )
    rows = list((await session.execute(stmt.order_by(Comment.created_at))).scalars().all())
    return CommentListOut(
        pursuit_id=pursuit.id,
        items=[CommentOut.model_validate(row, from_attributes=True) for row in rows],
        open_count=sum(1 for row in rows if row.resolved_at is None),
    )


@router.post("/{pursuit_id}/comments", response_model=CommentOut, status_code=201)
async def create_comment(
    pursuit_id: uuid.UUID,
    body: CommentIn,
    session: TenantSessionDep,
    user: ReaderDep,
    request: Request,
) -> JSONResponse:
    """Comment on a draft section, a compliance item or the scorecard."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    await _check_target(session, pursuit, body.target_type, body.target_id)
    await ensure_user_membership(
        user_id=user.id, email=user.email, tenant_id=user.tenant_id, role=user.role
    )
    comment = Comment(
        tenant_id=user.tenant_id,
        pursuit_id=pursuit.id,
        target_type=body.target_type,
        target_id=body.target_id,
        body=body.body,
        author_user_id=user.id,
    )
    session.add(comment)
    await session.flush()
    request.state.audit = AuditHint(
        action="comment.created",
        object_type="comment",
        object_id=str(comment.id),
        meta={
            "pursuit_id": str(pursuit.id),
            "target_type": body.target_type,
            "target_id": str(body.target_id),
        },
    )
    out = CommentOut.model_validate(comment, from_attributes=True)
    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))


@router.post("/{pursuit_id}/comments/{comment_id}/resolve", response_model=CommentOut)
async def resolve_comment(
    pursuit_id: uuid.UUID,
    comment_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    request: Request,
) -> CommentOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    comment = (
        await session.execute(
            select(Comment).where(Comment.id == comment_id, Comment.pursuit_id == pursuit.id)
        )
    ).scalar_one_or_none()
    if comment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="comment not found")
    if comment.resolved_at is None:  # resolving twice is a no-op, not an error
        comment.resolved_at = datetime.now(UTC)
    request.state.audit = AuditHint(
        action="comment.resolved",
        object_type="comment",
        object_id=str(comment.id),
        meta={"pursuit_id": str(pursuit.id)},
    )
    await session.flush()
    return CommentOut.model_validate(comment, from_attributes=True)
