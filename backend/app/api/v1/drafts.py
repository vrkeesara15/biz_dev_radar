"""Pursuit workspace API (SPEC 10.3, 10.4): draft sections, approvals and comments.

    GET  /api/v1/pursuits/{id}/drafts                      list + grounding counts
    GET  /api/v1/pursuits/{id}/drafts/{section_id}         one section's current version
    PUT  /api/v1/pursuits/{id}/drafts/{section_id}         optimistic save (base_version)
    POST /api/v1/pursuits/{id}/drafts/{section_id}/approve reviewer / bid manager / owner
    GET  /api/v1/pursuits/{id}/drafts/{section_id}/feedback human edit diffs (M5-17)

The comment routes this milestone added live in api/v1/collab.py, whose CRUD is the
richer one; only the open-comment count per section is read here.

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
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, TenantSessionDep, require_role
from app.core.collab import TARGET_DRAFT_SECTION
from app.core.markdown import markdown_to_html
from app.core.roles import Role
from app.models import Comment, Draft, DraftVersion, Pursuit
from app.models.drafts import AUTHOR_USER, DRAFT_STATUS_APPROVED
from app.services import draft_feedback as feedback_svc
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
AUDIT_DRAFT_FEEDBACK = "draft.feedback_read"


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


class DraftFeedbackOut(BaseModel):
    """One human edit of a section, kept as a unified diff (SPEC 8: edits are saved as
    feedback to improve future drafts). Tenant-scoped; never read across tenants."""

    id: uuid.UUID
    pursuit_id: uuid.UUID
    draft_id: uuid.UUID
    section_id: str
    from_version_id: uuid.UUID | None
    to_version_id: uuid.UUID | None
    from_author: str
    diff_text: str
    stats: dict[str, Any] = Field(default_factory=dict)
    edited_by: uuid.UUID | None
    created_at: datetime


class DraftFeedbackListOut(BaseModel):
    pursuit_id: uuid.UUID
    section_id: str
    items: list[DraftFeedbackOut] = Field(default_factory=list)
    count: int = 0


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
                Comment.target_type == TARGET_DRAFT_SECTION,
                Comment.resolved_at.is_(None),
            )
        )
    ).scalars()
    counts: dict[uuid.UUID, int] = {}
    for target_id in rows:
        if target_id is None:  # a draft_section comment always names its section
            continue
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


@router.get("/{pursuit_id}/drafts/{section_id}/feedback", response_model=DraftFeedbackListOut)
async def list_draft_feedback(
    pursuit_id: uuid.UUID,
    section_id: str,
    session: TenantSessionDep,
    user: ReaderDep,
) -> DraftFeedbackListOut:
    """The human edits of this section, newest first, as unified diffs (SPEC 8).

    Reading them is reading draft text, so it is audited like any other draft read and
    the viewer role cannot see it. Feedback never leaves its tenant.
    """
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    draft = await _load_draft(session, pursuit, section_id)
    rows = await feedback_svc.for_section(session, pursuit.id, section_id)
    await audit(
        session,
        AUDIT_DRAFT_FEEDBACK,
        draft,
        user_id=user.id,
        meta={"pursuit_id": str(pursuit.id), "section_id": section_id, "count": len(rows)},
    )
    await session.commit()
    return DraftFeedbackListOut(
        pursuit_id=pursuit.id,
        section_id=section_id,
        items=[DraftFeedbackOut.model_validate(row, from_attributes=True) for row in rows],
        count=len(rows),
    )
