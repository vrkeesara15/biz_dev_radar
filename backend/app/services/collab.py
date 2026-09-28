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

from app.agents.tools import PursuitScope, enforce
from app.core.collab import (
    MAX_TITLE,
    SOURCE_AGENT,
    TASK_DONE,
    TASK_OPEN,
    find_placeholders,
    placeholder_key,
    task_title,
)
from app.models import Comment, Pursuit, Task

PLACEHOLDER_REF = "placeholder"
AGENT_REF = "agent"


def task_statement(
    pursuit_id: uuid.UUID,
    *,
    status: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
) -> Select[Task]:
    stmt = select(Task).where(Task.pursuit_id == pursuit_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if assignee_user_id is not None:
        stmt = stmt.where(Task.assignee_user_id == assignee_user_id)
    return stmt.order_by(Task.status, Task.due_at.asc().nullslast(), Task.created_at)


async def list_tasks(
    session: AsyncSession,
    pursuit_id: uuid.UUID,
    *,
    status: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
) -> Sequence[Task]:
    stmt = task_statement(pursuit_id, status=status, assignee_user_id=assignee_user_id)
    return (await session.execute(stmt)).scalars().all()


async def list_comments(
    session: AsyncSession,
    pursuit_id: uuid.UUID,
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    include_resolved: bool = True,
    resolved: bool | None = None,
) -> Sequence[Comment]:
    stmt = select(Comment).where(Comment.pursuit_id == pursuit_id)
    if target_type is not None:
        stmt = stmt.where(Comment.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(Comment.target_id == target_id)
    if not include_resolved:
        stmt = stmt.where(Comment.resolved_at.is_(None))
    if resolved is not None:
        stmt = stmt.where(
            Comment.resolved_at.is_not(None) if resolved else Comment.resolved_at.is_(None)
        )
    stmt = stmt.order_by(Comment.created_at)
    return (await session.execute(stmt)).scalars().all()


async def find_placeholder_task(
    session: AsyncSession, pursuit_id: uuid.UUID, agent: str, placeholder: str
) -> Task | None:
    """The task this exact ask already produced, whatever its status."""
    key = placeholder_key(placeholder)
    rows = (
        (
            await session.execute(
                select(Task).where(Task.pursuit_id == pursuit_id, Task.source == SOURCE_AGENT)
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
    title: str | None = None,
    scope: PursuitScope | None = None,
) -> tuple[Task, bool]:
    """One open task for one `[NEEDS INPUT: ...]` ask. Returns (task, created).

    Idempotent per (pursuit, agent, placeholder): a re-run of the agent finds the task it
    made last time, leaving a completed one completed. `title` overrides the default
    "Provide: <placeholder>" wording where the agent has a better sentence (the drafters
    prefix the section), and `scope` is the M5-12 guard that stops an agent writing a
    task at a sibling pursuit of the same tenant.
    """
    enforce(scope, tenant_id=pursuit.tenant_id, pursuit_id=pursuit.id)
    existing = await find_placeholder_task(session, pursuit.id, agent, placeholder)
    if existing is not None:
        return existing, False
    task = Task(
        tenant_id=pursuit.tenant_id,
        pursuit_id=pursuit.id,
        title=(title or task_title(placeholder))[:MAX_TITLE],
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
    scope: PursuitScope | None = None,
) -> list[Task]:
    """Every distinct placeholder in `text` as a task; returns only the NEW ones."""
    created: list[Task] = []
    for placeholder in find_placeholders(text):
        task, is_new = await create_task_from_placeholder(
            session,
            pursuit,
            placeholder,
            agent=agent,
            assignee_user_id=assignee_user_id,
            ref=ref,
            scope=scope,
        )
        if is_new:
            created.append(task)
    return created


def complete(task: Task, user_id: uuid.UUID, *, now: datetime | None = None) -> None:
    task.status = TASK_DONE
    task.completed_at = now or datetime.now(UTC)
    task.completed_by = user_id


def reopen(task: Task) -> None:
    task.status = TASK_OPEN
    task.completed_at = None
    task.completed_by = None


async def open_task_count(session: AsyncSession, pursuit_id: uuid.UUID) -> int:
    return len(await list_tasks(session, pursuit_id, status=TASK_OPEN))
