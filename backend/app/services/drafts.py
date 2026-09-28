"""Draft persistence (SPEC 8 agent 6, 10.2, 10.3).

`save_version` is the ONLY place a draft_versions row is written -- by the drafting
agent and by a writer's PUT alike -- so every version is numbered, run through the
grounding validator (core.grounding, M5-11) and repointed as
`drafts.current_version_id` in one place.

    draft, version = await save_version(session, tenant_id, pursuit_id, "technical-approach",
                                        title="Technical Approach", body_html=..., author="agent")
    draft, version = await latest(session, pursuit_id, "technical-approach")
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import PursuitScope, enforce
from app.core.citations import kb_token
from app.core.collab import SOURCE_AGENT, TARGET_TYPES, TASK_OPEN
from app.core.diffs import diff_stats, unified_diff
from app.core.grounding import validate
from app.core.html_text import html_to_text
from app.core.markdown import sanitize_html
from app.models import (
    Certification,
    Comment,
    CompanyProfile,
    Draft,
    DraftFeedback,
    DraftVersion,
    KBChunk,
    PastPerformance,
    Pursuit,
    Task,
)
from app.models.drafts import (
    AUTHOR_AGENT,
    AUTHOR_USER,
    DRAFT_STATUS_APPROVED,
    DRAFT_STATUS_DRAFT,
    DRAFT_STATUS_IN_REVIEW,
)
from app.services.evidence import evidence_tokens, load_evidence


@dataclass(frozen=True, slots=True)
class GroundingInputs:
    """What core.grounding checks a body against: the tokens that resolve to one of this
    tenant's own records, and the facts those records state."""

    tokens: frozenset[str] = frozenset()
    facts: tuple[str, ...] = ()


async def grounding_inputs(session: AsyncSession, pursuit_id: uuid.UUID) -> GroundingInputs:
    """Every citation token the pursuit's profile can back, plus its plain facts."""
    pursuit = await session.get(Pursuit, pursuit_id)
    if pursuit is None:
        return GroundingInputs()
    records = await load_evidence(session, pursuit.profile_id)
    tokens = set(evidence_tokens(records))
    facts: list[str] = [record.title for record in records]
    chunks = (
        await session.execute(
            select(KBChunk.source_type, KBChunk.source_id, KBChunk.chunk_index).where(
                KBChunk.profile_id == pursuit.profile_id
            )
        )
    ).all()
    tokens.update(kb_token(str(st), sid, int(index)) for st, sid, index in chunks)
    profile = await session.get(CompanyProfile, pursuit.profile_id)
    if profile is not None:
        facts.append(profile.legal_name)
        facts.extend(str(name) for name in (profile.dba_names or []))
    customers = (
        await session.execute(
            select(PastPerformance.customer).where(PastPerformance.profile_id == pursuit.profile_id)
        )
    ).scalars()
    facts.extend(str(customer) for customer in customers if customer)
    certs = (
        await session.execute(
            select(Certification.kind).where(Certification.profile_id == pursuit.profile_id)
        )
    ).scalars()
    # the certification rows are already citable (load_evidence); their names are facts, so
    # naming a certification the profile really holds is not a third-party reference
    facts.extend(str(kind).replace("_", " ") for kind in certs)
    return GroundingInputs(tokens=frozenset(tokens), facts=tuple(f for f in facts if f))


async def get_draft(session: AsyncSession, pursuit_id: uuid.UUID, section_id: str) -> Draft | None:
    return (
        await session.execute(
            select(Draft).where(Draft.pursuit_id == pursuit_id, Draft.section_id == section_id)
        )
    ).scalar_one_or_none()


async def list_drafts(session: AsyncSession, pursuit_id: uuid.UUID) -> list[Draft]:
    return list(
        (
            await session.execute(
                select(Draft)
                .where(Draft.pursuit_id == pursuit_id)
                .order_by(Draft.volume, Draft.section_id)
            )
        )
        .scalars()
        .all()
    )


async def get_version(
    session: AsyncSession, draft: Draft, version_id: uuid.UUID | None = None
) -> DraftVersion | None:
    target = version_id or draft.current_version_id
    if target is None:
        return None
    return (
        await session.execute(
            select(DraftVersion).where(DraftVersion.id == target, DraftVersion.draft_id == draft.id)
        )
    ).scalar_one_or_none()


async def versions(session: AsyncSession, draft_id: uuid.UUID) -> list[DraftVersion]:
    return list(
        (
            await session.execute(
                select(DraftVersion)
                .where(DraftVersion.draft_id == draft_id)
                .order_by(DraftVersion.version)
            )
        )
        .scalars()
        .all()
    )


async def latest_version_number(session: AsyncSession, draft_id: uuid.UUID) -> int:
    current: int | None = (
        await session.execute(
            select(func.max(DraftVersion.version)).where(DraftVersion.draft_id == draft_id)
        )
    ).scalar_one()
    return int(current or 0)


async def save_version(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    section_id: str,
    *,
    title: str,
    body_html: str,
    volume: str | None = None,
    citations: Sequence[dict[str, Any]] = (),
    needs_input: Sequence[dict[str, Any]] = (),
    # extra keys merged over the grounding report (the red-team reviewer adds its own)
    flags: dict[str, Any] | None = None,
    # SPEC 11: the agent run's PursuitScope; a write outside it raises ScopeViolation
    scope: PursuitScope | None = None,
    author: str = AUTHOR_AGENT,
    author_user_id: uuid.UUID | None = None,
    model: str | None = None,
    tokens: int = 0,
) -> tuple[Draft, DraftVersion]:
    """Append a version to the section's draft (creating the draft row on first write).

    The HTML is sanitised here whoever wrote it, `body_text` is derived from it, the
    grounding validator runs over the result (SPEC 8: unsupported company claims are
    flagged for the UI) and the draft's `current_version_id` is repointed at the new row.
    """
    enforce(scope, tenant_id=tenant_id, pursuit_id=pursuit_id)
    draft = await get_draft(session, pursuit_id, section_id)
    previous: DraftVersion | None = None
    if draft is not None:
        previous = await get_version(session, draft)
    if draft is None:
        draft = Draft(
            tenant_id=tenant_id,
            pursuit_id=pursuit_id,
            section_id=section_id,
            title=title,
            volume=volume,
            status=DRAFT_STATUS_DRAFT,
        )
        session.add(draft)
        await session.flush()
    else:
        draft.title = title or draft.title
        if volume is not None:
            draft.volume = volume
    clean = sanitize_html(body_html)
    body_text = html_to_text(clean)
    grounding = await grounding_inputs(session, pursuit_id)
    report = validate(body_text, list(citations), grounding.tokens, grounding.facts)
    row = DraftVersion(
        tenant_id=tenant_id,
        draft_id=draft.id,
        version=await latest_version_number(session, draft.id) + 1,
        body_html=clean,
        body_text=body_text,
        citations=[dict(c) for c in citations],
        needs_input=[dict(n) for n in needs_input],
        flags={**report.as_dict(), **dict(flags or {})},
        author=author,
        author_user_id=author_user_id,
        model=model,
        tokens=tokens,
    )
    session.add(row)
    await session.flush()
    draft.current_version_id = row.id
    await session.flush()
    if author == AUTHOR_USER and previous is not None:
        await record_feedback(session, draft, previous, row, edited_by=author_user_id)
    return draft, row


async def record_feedback(
    session: AsyncSession,
    draft: Draft,
    previous: DraftVersion,
    current: DraftVersion,
    *,
    edited_by: uuid.UUID | None = None,
) -> DraftFeedback | None:
    """Keep a human edit as a unified diff against the version it started from.

    SPEC 8: "human-in-the-loop edits are diffed and saved as feedback to improve future
    drafts". An edit that changed nothing in the rendered text is not feedback, so it is
    not stored.
    """
    patch = unified_diff(
        previous.body_text,
        current.body_text,
        before_label=f"v{previous.version} ({previous.author})",
        after_label=f"v{current.version} ({current.author})",
    )
    stats = diff_stats(patch)
    if not stats.changed:
        return None
    row = DraftFeedback(
        tenant_id=draft.tenant_id,
        pursuit_id=draft.pursuit_id,
        draft_id=draft.id,
        section_id=draft.section_id,
        from_version_id=previous.id,
        to_version_id=current.id,
        from_author=previous.author,
        diff_text=patch,
        stats=dict(stats.as_dict()),
        edited_by=edited_by,
    )
    session.add(row)
    await session.flush()
    return row


async def create_task(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    *,
    title: str,
    ref: dict[str, Any] | None = None,
    source: str = SOURCE_AGENT,
    assignee_user_id: uuid.UUID | None = None,
    scope: PursuitScope | None = None,
) -> Task:
    """A piece of work an agent hands back to a human (SPEC 8: [NEEDS INPUT] -> task)."""
    enforce(scope, tenant_id=tenant_id, pursuit_id=pursuit_id)
    task = Task(
        tenant_id=tenant_id,
        pursuit_id=pursuit_id,
        title=title[:2000],
        assignee_user_id=assignee_user_id,
        status=TASK_OPEN,
        source=source,
        ref=dict(ref or {}),
    )
    session.add(task)
    await session.flush()
    return task


async def add_comment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    *,
    target_type: str,
    target_id: uuid.UUID | None,
    body: str,
    author_user_id: uuid.UUID | None = None,
    scope: PursuitScope | None = None,
) -> Comment:
    """The only insert point for a review comment (a reviewer's POST and the red-team
    agent's remaining issues both land here)."""
    enforce(scope, tenant_id=tenant_id, pursuit_id=pursuit_id)
    if target_type not in TARGET_TYPES:
        raise ValueError(f"unknown comment target {target_type!r}; one of {TARGET_TYPES}")
    comment = Comment(
        tenant_id=tenant_id,
        pursuit_id=pursuit_id,
        target_type=target_type,
        target_id=target_id,
        body=body[:10_000],
        author_user_id=author_user_id,
    )
    session.add(comment)
    await session.flush()
    return comment


async def open_tasks(session: AsyncSession, pursuit_id: uuid.UUID) -> list[Task]:
    return list(
        (
            await session.execute(
                select(Task)
                .where(Task.pursuit_id == pursuit_id, Task.status == TASK_OPEN)
                .order_by(Task.created_at)
            )
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True, slots=True)
class DraftsSummary:
    """What the pursuit header shows about its drafts (SPEC 8: the unsupported-claim
    count travels with the pursuit, not only with the section)."""

    count: int = 0
    approved: int = 0
    in_review: int = 0
    unsupported_claims_count: int = 0
    needs_input_count: int = 0
    flagged_sections: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "count": self.count,
            "approved": self.approved,
            "in_review": self.in_review,
            "unsupported_claims_count": self.unsupported_claims_count,
            "needs_input_count": self.needs_input_count,
            "flagged_sections": self.flagged_sections,
        }


async def summarise(session: AsyncSession, pursuit_id: uuid.UUID) -> DraftsSummary:
    """Aggregate the pursuit's CURRENT draft versions (older versions do not count)."""
    rows = (
        await session.execute(
            select(Draft, DraftVersion)
            .outerjoin(DraftVersion, DraftVersion.id == Draft.current_version_id)
            .where(Draft.pursuit_id == pursuit_id)
        )
    ).all()
    unsupported = needs_input = flagged = approved = in_review = 0
    for row in rows:
        draft = row[0]
        version: DraftVersion | None = row[1]
        if draft.status == DRAFT_STATUS_APPROVED:
            approved += 1
        elif draft.status == DRAFT_STATUS_IN_REVIEW:
            in_review += 1
        if version is None:  # a draft row whose first version is still being written
            continue
        count = int((version.flags or {}).get("unsupported_count") or 0)
        unsupported += count
        flagged += 1 if count else 0
        needs_input += len(version.needs_input or [])
    return DraftsSummary(
        count=len(rows),
        approved=approved,
        in_review=in_review,
        unsupported_claims_count=unsupported,
        needs_input_count=needs_input,
        flagged_sections=flagged,
    )
