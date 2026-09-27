"""Tasks and comments on a pursuit (SPEC 8, 9, 10.4 screen 6; M6-07).

    GET    /api/v1/pursuits/{id}/tasks?status=&assignee=
    POST   /api/v1/pursuits/{id}/tasks           {title, detail?, assignee_user_id?, due_at?}
    PATCH  /api/v1/pursuits/{id}/tasks/{task_id} {title?, assignee_user_id?, due_at?, status?}
    DELETE /api/v1/pursuits/{id}/tasks/{task_id}

    GET    /api/v1/pursuits/{id}/comments?target_type=&target_id=&unresolved=
    POST   /api/v1/pursuits/{id}/comments        {body, target_type?, target_id?}
    PATCH  /api/v1/pursuits/{id}/comments/{comment_id} {body?, resolved?}
    DELETE /api/v1/pursuits/{id}/comments/{comment_id}

Roles (SPEC 3): a reviewer may comment and resolve but not create or reassign work; a
writer owns the task list; a viewer reads. Anybody who is assigned a task may close it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.core.collab import (
    SOURCE_USER,
    TARGET_PURSUIT,
    TARGET_TYPES,
    TASK_DONE,
    TASK_OPEN,
    TASK_STATUSES,
)
from app.core.roles import Role
from app.models import PursuitComment, PursuitTask, User
from app.services import collab as collab_svc
from app.services import pursuits as pursuit_svc
from app.services.audit import AuditHint

router = APIRouter(prefix="/pursuits", tags=["pursuits"])

MANAGER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER)
WRITER_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER)
# SPEC 3 reviewer row: "draft.comment" and "section.approve"
COMMENT_ROLES = (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER)

WriterDep = Annotated[CurrentUser, Depends(require_role(*WRITER_ROLES))]
CommenterDep = Annotated[CurrentUser, Depends(require_role(*COMMENT_ROLES))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]
# Closing a task is open to every tenant role: the route still refuses a task that is
# neither yours nor yours to manage.
TaskActorDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]

MAX_BODY = 20_000


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    detail: str | None = Field(default=None, max_length=MAX_BODY)
    assignee_user_id: uuid.UUID | None = None
    due_at: datetime | None = None

    @field_validator("due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("due_at must carry a time zone offset")
        return None if value is None else value.astimezone(UTC)


class TaskPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    detail: str | None = Field(default=None, max_length=MAX_BODY)
    assignee_user_id: uuid.UUID | None = None
    due_at: datetime | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str | None) -> str | None:
        if value is not None and value not in TASK_STATUSES:
            raise ValueError(f"unknown status {value!r}; one of {', '.join(TASK_STATUSES)}")
        return value

    @field_validator("due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("due_at must carry a time zone offset")
        return None if value is None else value.astimezone(UTC)


class TaskOut(BaseModel):
    id: uuid.UUID
    pursuit_id: uuid.UUID
    title: str
    detail: str | None
    assignee_user_id: uuid.UUID | None
    due_at: datetime | None
    status: str
    source: str
    ref: dict[str, Any]
    created_by: uuid.UUID | None
    completed_at: datetime | None
    completed_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class TaskListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[TaskOut]
    open_count: int


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY)
    target_type: str = TARGET_PURSUIT
    target_id: uuid.UUID | None = None

    @field_validator("target_type")
    @classmethod
    def _known_target(cls, value: str) -> str:
        if value not in TARGET_TYPES:
            raise ValueError(f"unknown target_type {value!r}; one of {', '.join(TARGET_TYPES)}")
        return value


class CommentPatchIn(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY)
    resolved: bool | None = None


class CommentOut(BaseModel):
    id: uuid.UUID
    pursuit_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID | None
    body: str
    author_user_id: uuid.UUID | None
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class CommentListOut(BaseModel):
    pursuit_id: uuid.UUID
    items: list[CommentOut]
    unresolved_count: int


def task_out(row: PursuitTask) -> TaskOut:
    return TaskOut(
        id=row.id,
        pursuit_id=row.pursuit_id,
        title=row.title,
        detail=row.detail,
        assignee_user_id=row.assignee_user_id,
        due_at=row.due_at,
        status=row.status,
        source=row.source,
        ref=dict(row.ref or {}),
        created_by=row.created_by,
        completed_at=row.completed_at,
        completed_by=row.completed_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def comment_out(row: PursuitComment) -> CommentOut:
    return CommentOut(
        id=row.id,
        pursuit_id=row.pursuit_id,
        target_type=row.target_type,
        target_id=row.target_id,
        body=row.body,
        author_user_id=row.author_user_id,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _task(session: AsyncSession, pursuit_id: uuid.UUID, task_id: uuid.UUID) -> PursuitTask:
    await pursuit_svc.get_pursuit(session, pursuit_id)
    row = await session.get(PursuitTask, task_id)
    if row is None or row.pursuit_id != pursuit_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="task not found")
    return row


async def _comment(
    session: AsyncSession, pursuit_id: uuid.UUID, comment_id: uuid.UUID
) -> PursuitComment:
    await pursuit_svc.get_pursuit(session, pursuit_id)
    row = await session.get(PursuitComment, comment_id)
    if row is None or row.pursuit_id != pursuit_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="comment not found")
    return row


async def _known_user(session: AsyncSession, user_id: uuid.UUID | None) -> uuid.UUID | None:
    if user_id is None:
        return None
    if await session.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="assignee not found")
    return user_id


# --- tasks ---------------------------------------------------------------------------------


@router.get("/{pursuit_id}/tasks", response_model=TaskListOut)
async def list_tasks(
    pursuit_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    task_status: Annotated[str | None, Query(alias="status")] = None,
    assignee: uuid.UUID | None = None,
) -> TaskListOut:
    """Open tasks first, then by due date (SPEC 10.4 screen 6, Tasks tab)."""
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    if task_status is not None and task_status not in TASK_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown status {task_status!r}; one of {', '.join(TASK_STATUSES)}",
        )
    rows = await collab_svc.list_tasks(
        session, pursuit.id, status=task_status, assignee_user_id=assignee
    )
    return TaskListOut(
        pursuit_id=pursuit.id,
        items=[task_out(row) for row in rows],
        open_count=sum(1 for row in rows if row.status == TASK_OPEN),
    )


@router.post("/{pursuit_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    pursuit_id: uuid.UUID,
    body: TaskIn,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> TaskOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    row = PursuitTask(
        tenant_id=user.tenant_id,
        pursuit_id=pursuit.id,
        title=body.title,
        detail=body.detail,
        assignee_user_id=await _known_user(session, body.assignee_user_id),
        due_at=body.due_at,
        status=TASK_OPEN,
        source=SOURCE_USER,
        ref={},
        created_by=user.id,
    )
    session.add(row)
    pursuit_svc.touch(pursuit)
    await session.flush()
    request.state.audit = AuditHint(
        action="pursuit.task_created",
        object_type="pursuit_task",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit.id)},
    )
    return task_out(row)


@router.patch("/{pursuit_id}/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    pursuit_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskPatchIn,
    session: TenantSessionDep,
    user: TaskActorDep,
    request: Request,
) -> TaskOut:
    """Edit or close a task. A reviewer / viewer may only close one assigned to them."""
    row = await _task(session, pursuit_id, task_id)
    edits = body.model_dump(exclude_unset=True)
    only_status = set(edits) <= {"status"}
    may_edit = user.role in WRITER_ROLES
    if not may_edit and not (only_status and row.assignee_user_id == user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"role {user.role.value} may only close a task assigned to them",
        )
    meta: dict[str, Any] = {"pursuit_id": str(pursuit_id)}
    if may_edit:
        if body.title is not None:
            row.title = body.title
        if "detail" in edits:
            row.detail = body.detail
        if "assignee_user_id" in edits:
            row.assignee_user_id = await _known_user(session, body.assignee_user_id)
            meta["assignee_user_id"] = str(row.assignee_user_id)
        if "due_at" in edits:
            row.due_at = body.due_at
    if body.status is not None and body.status != row.status:
        if body.status == TASK_DONE:
            collab_svc.complete(row, user.id)
        else:
            collab_svc.reopen(row)
        meta["status"] = row.status
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    pursuit_svc.touch(pursuit)
    await session.flush()
    await session.refresh(row)
    request.state.audit = AuditHint(
        action="pursuit.task_updated",
        object_type="pursuit_task",
        object_id=str(row.id),
        meta=meta,
    )
    return task_out(row)


@router.delete("/{pursuit_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    pursuit_id: uuid.UUID,
    task_id: uuid.UUID,
    session: TenantSessionDep,
    user: WriterDep,
    request: Request,
) -> None:
    row = await _task(session, pursuit_id, task_id)
    request.state.audit = AuditHint(
        action="pursuit.task_deleted",
        object_type="pursuit_task",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit_id)},
    )
    await session.delete(row)
    await session.flush()


# --- comments ------------------------------------------------------------------------------


@router.get("/{pursuit_id}/comments", response_model=CommentListOut)
async def list_comments(
    pursuit_id: uuid.UUID,
    session: TenantSessionDep,
    user: ReaderDep,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    unresolved: bool = False,
) -> CommentListOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    if target_type is not None and target_type not in TARGET_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown target_type {target_type!r}; one of {', '.join(TARGET_TYPES)}",
        )
    rows = await collab_svc.list_comments(
        session,
        pursuit.id,
        target_type=target_type,
        target_id=target_id,
        include_resolved=not unresolved,
    )
    return CommentListOut(
        pursuit_id=pursuit.id,
        items=[comment_out(row) for row in rows],
        unresolved_count=sum(1 for row in rows if row.resolved_at is None),
    )


@router.post(
    "/{pursuit_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED
)
async def create_comment(
    pursuit_id: uuid.UUID,
    body: CommentIn,
    session: TenantSessionDep,
    user: CommenterDep,
    request: Request,
) -> CommentOut:
    pursuit = await pursuit_svc.get_pursuit(session, pursuit_id)
    row = PursuitComment(
        tenant_id=user.tenant_id,
        pursuit_id=pursuit.id,
        target_type=body.target_type,
        target_id=body.target_id,
        body=body.body,
        author_user_id=user.id,
    )
    session.add(row)
    pursuit_svc.touch(pursuit)
    await session.flush()
    request.state.audit = AuditHint(
        action="pursuit.comment_created",
        object_type="pursuit_comment",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit.id), "target_type": row.target_type},
    )
    return comment_out(row)


@router.patch("/{pursuit_id}/comments/{comment_id}", response_model=CommentOut)
async def update_comment(
    pursuit_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: CommentPatchIn,
    session: TenantSessionDep,
    user: CommenterDep,
    request: Request,
) -> CommentOut:
    """Edit your own comment; anybody who may comment can resolve or reopen a thread."""
    row = await _comment(session, pursuit_id, comment_id)
    meta: dict[str, Any] = {"pursuit_id": str(pursuit_id)}
    if body.body is not None:
        if row.author_user_id != user.id and user.role not in MANAGER_ROLES:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="only the author may edit a comment"
            )
        row.body = body.body
        meta["edited"] = True
    if body.resolved is not None:
        row.resolved_at = datetime.now(UTC) if body.resolved else None
        row.resolved_by = user.id if body.resolved else None
        meta["resolved"] = body.resolved
    await session.flush()
    await session.refresh(row)
    request.state.audit = AuditHint(
        action="pursuit.comment_updated",
        object_type="pursuit_comment",
        object_id=str(row.id),
        meta=meta,
    )
    return comment_out(row)


@router.delete("/{pursuit_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    pursuit_id: uuid.UUID,
    comment_id: uuid.UUID,
    session: TenantSessionDep,
    user: CommenterDep,
    request: Request,
) -> None:
    row = await _comment(session, pursuit_id, comment_id)
    if row.author_user_id != user.id and user.role not in MANAGER_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only the author or a bid manager may delete"
        )
    request.state.audit = AuditHint(
        action="pursuit.comment_deleted",
        object_type="pursuit_comment",
        object_id=str(row.id),
        meta={"pursuit_id": str(pursuit_id)},
    )
    await session.delete(row)
    await session.flush()
