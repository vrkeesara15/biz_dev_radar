"""Draft persistence (SPEC 8 agent 6, 10.2, 10.3).

`save_version` is the ONLY place a draft_versions row is written -- by the drafting
agent and by a writer's PUT alike -- so every version is numbered, grounded (M5-11 runs
the validator here) and repointed as `drafts.current_version_id` in one place.

    draft, version = await save_version(session, tenant_id, pursuit_id, "technical-approach",
                                        title="Technical Approach", body_html=..., author="agent")
    draft, version = await latest(session, pursuit_id, "technical-approach")
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.html_text import html_to_text
from app.core.markdown import sanitize_html
from app.models import Draft, DraftVersion, Task
from app.models.drafts import AUTHOR_AGENT, DRAFT_STATUS_DRAFT, TASK_OPEN


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
    flags: dict[str, Any] | None = None,
    author: str = AUTHOR_AGENT,
    author_user_id: uuid.UUID | None = None,
    model: str | None = None,
    tokens: int = 0,
) -> tuple[Draft, DraftVersion]:
    """Append a version to the section's draft (creating the draft row on first write).

    The HTML is sanitised here whoever wrote it, `body_text` is derived from it and the
    draft's `current_version_id` is repointed at the new row.
    """
    draft = await get_draft(session, pursuit_id, section_id)
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
    row = DraftVersion(
        tenant_id=tenant_id,
        draft_id=draft.id,
        version=await latest_version_number(session, draft.id) + 1,
        body_html=clean,
        body_text=html_to_text(clean),
        citations=[dict(c) for c in citations],
        needs_input=[dict(n) for n in needs_input],
        flags=dict(flags or {}),
        author=author,
        author_user_id=author_user_id,
        model=model,
        tokens=tokens,
    )
    session.add(row)
    await session.flush()
    draft.current_version_id = row.id
    await session.flush()
    return draft, row


async def create_task(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pursuit_id: uuid.UUID,
    *,
    title: str,
    ref: dict[str, Any] | None = None,
    source: str = AUTHOR_AGENT,
    assignee_user_id: uuid.UUID | None = None,
) -> Task:
    """A piece of work an agent hands back to a human (SPEC 8: [NEEDS INPUT] -> task)."""
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
