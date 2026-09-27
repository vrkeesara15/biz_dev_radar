"""Tasks and comments on a pursuit (SPEC 8, 9; M6-07).

    tasks = await create_tasks_from_placeholders(
        session, pursuit, "[NEEDS INPUT: labor category]", agent="pricing"
    )
    task = await create_task_from_placeholder(session, pursuit, "price", agent="pricing")

The drafters and the pricing agent leave `[NEEDS INPUT: ...]` wherever they refuse to
invent a fact; each distinct placeholder becomes one open task addressed to the pursuit
owner. The helper is idempotent per (pursuit, agent, placeholder): re-running an agent
re-uses the open task and never asks twice, and a task somebody already completed is
left completed rather than re-opened.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.collab import (
    SOURCE_AGENT,
    TASK_DONE,
    TASK_OPEN,
    find_placeholders,
    placeholder_key,
    task_title,
)
from app.models import Pursuit, PursuitComment, PursuitTask

PLACEHOLDER_REF = "placeholder"
AGENT_REF = "agent"


def task_statement(
    pursuit_id: uuid.UUID,
    *,
    status: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
) -> Select[PursuitTask]:
    stmt = select(PursuitTask).where(PursuitTask.pursuit_id == pursuit_id)
    if status is not None:
        stmt = stmt.where(PursuitTask.status == status)
    if assignee_user_id is not None:
        stmt = stmt.where(PursuitTask.assignee_user_id == assignee_user_id)
    return stmt.order_by(
        PursuitTask.status, PursuitTask.due_at.asc().nullslast(), PursuitTask.created_at
    )


async def list_tasks(
    session: AsyncSession,
    pursuit_id: uuid.UUID,
    *,
    status: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
) -> Sequence[PursuitTask]:
    stmt = task_statement(pursuit_id, status=status, assignee_user_id=assignee_user_id)
    return (await session.execute(stmt)).scalars().all()


async def list_comments(
    session: AsyncSession,
    pursuit_id: uuid.UUID,
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    include_resolved: bool = True,
) -> Sequence[PursuitComment]:
    stmt = select(PursuitComment).where(PursuitComment.pursuit_id == pursuit_id)
    if target_type is not None:
        stmt = stmt.where(PursuitComment.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(PursuitComment.target_id == target_id)
    if not include_resolved:
        stmt = stmt.where(PursuitComment.resolved_at.is_(None))
    stmt = stmt.order_by(PursuitComment.created_at)
    return (await session.execute(stmt)).scalars().all()


async def find_placeholder_task(
    session: AsyncSession, pursuit_id: uuid.UUID, agent: str, placeholder: str
) -> PursuitTask | None:
    """The task this exact ask already produced, whatever its status."""
    key = placeholder_key(placeholder)
    rows = (
        (
            await session.execute(
                select(PursuitTask).where(
                    PursuitTask.pursuit_id == pursuit_id, PursuitTask.source == SOURCE_AGENT
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        ref = row.ref or {}
        if (
            ref.get(AGENT_REF) == agent
            and placeholder_key(str(ref.get(PLACEHOLDER_REF, ""))) == key
        ):
            return row
    return None


async def create_task_from_placeholder(
    session: AsyncSession,
    pursuit: Pursuit,
    placeholder: str,
    *,
    agent: str,
    assignee_user_id: uuid.UUID | None = None,
    due_at: datetime | None = None,
    ref: dict[str, Any] | None = None,
) -> tuple[PursuitTask, bool]:
    """One open task for one `[NEEDS INPUT: ...]` ask. Returns (task, created).

    Idempotent per (pursuit, agent, placeholder): a re-run of the agent finds the task it
    made last time, leaving a completed one completed.
    """
    existing = await find_placeholder_task(session, pursuit.id, agent, placeholder)
    if existing is not None:
        return existing, False
    task = PursuitTask(
        tenant_id=pursuit.tenant_id,
        pursuit_id=pursuit.id,
        title=task_title(placeholder),
        assignee_user_id=assignee_user_id or pursuit.owner_user_id,
        due_at=due_at or pursuit.internal_due_at,
        status=TASK_OPEN,
        source=SOURCE_AGENT,
        ref={PLACEHOLDER_REF: " ".join(placeholder.split()), AGENT_REF: agent, **(ref or {})},
    )
    session.add(task)
    await session.flush()
    return task, True


async def create_tasks_from_placeholders(
    session: AsyncSession,
    pursuit: Pursuit,
    text: str | None,
    *,
    agent: str,
    assignee_user_id: uuid.UUID | None = None,
    ref: dict[str, Any] | None = None,
) -> list[PursuitTask]:
    """Every distinct placeholder in `text` as a task; returns only the NEW ones."""
    created: list[PursuitTask] = []
    for placeholder in find_placeholders(text):
        task, is_new = await create_task_from_placeholder(
            session, pursuit, placeholder, agent=agent, assignee_user_id=assignee_user_id, ref=ref
        )
        if is_new:
            created.append(task)
    return created


def complete(task: PursuitTask, user_id: uuid.UUID, *, now: datetime | None = None) -> None:
    task.status = TASK_DONE
    task.completed_at = now or datetime.now(UTC)
    task.completed_by = user_id


def reopen(task: PursuitTask) -> None:
    task.status = TASK_OPEN
    task.completed_at = None
    task.completed_by = None


async def open_task_count(session: AsyncSession, pursuit_id: uuid.UUID) -> int:
    return len(await list_tasks(session, pursuit_id, status=TASK_OPEN))
