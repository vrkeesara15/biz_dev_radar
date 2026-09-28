"""M6-07: tasks and comments on a pursuit, role checks, and [NEEDS INPUT] -> tasks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import (
    CompanyProfile,
    Membership,
    Opportunity,
    OpportunityDocument,
    Pursuit,
    Requirement,
    Task,
)
from app.services.collab import (
    create_task_from_placeholder,
    create_tasks_from_placeholders,
    open_task_count,
)
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)


async def _setup(database: Database) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Collab LLC")
        writer, reviewer, viewer = make_user(), make_user(), make_user()
        session.add_all([profile, writer, reviewer, viewer])
        await session.flush()
        for user, role in (
            (writer, Role.WRITER),
            (reviewer, Role.REVIEWER),
            (viewer, Role.VIEWER),
        ):
            session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"collab-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Collaborative notice",
            source_tz="America/New_York",
            response_due_at=DUE,
        )
        session.add(opportunity)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opportunity.id,
            owner_user_id=owner.id,
            created_by=owner.id,
            internal_due_at=DUE - timedelta(hours=48),
        )
        session.add(pursuit)
        await session.flush()
        # a real matrix row to anchor a comment on: since the merge, a comment may only
        # point at something that exists inside this pursuit (M5-16's rule)
        document = OpportunityDocument(
            opportunity_id=opportunity.id, url="https://x.test/rfp.pdf", file_name="rfp.pdf"
        )
        session.add(document)
        await session.flush()
        requirement = Requirement(
            tenant_id=tenant.id,
            pursuit_id=pursuit.id,
            req_id="R-004",
            text="The contractor shall provide a transition plan.",
            document_id=document.id,
            page=4,
            type="shall",
            quote="shall provide a transition plan",
        )
        session.add(requirement)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "requirement_id": requirement.id,
            "owner_id": owner.id,
            "writer_id": writer.id,
            "reviewer_id": reviewer.id,
            "viewer_id": viewer.id,
            "pursuit_id": pursuit.id,
        }


def _headers(ctx: dict[str, Any], role: Role = Role.TENANT_OWNER) -> dict[str, str]:
    ids = {
        Role.TENANT_OWNER: ctx["owner_id"],
        Role.BID_MANAGER: ctx["owner_id"],
        Role.WRITER: ctx["writer_id"],
        Role.REVIEWER: ctx["reviewer_id"],
        Role.VIEWER: ctx["viewer_id"],
    }
    return auth_headers(user_id=ids[role], tenant_id=ctx["tenant_id"], role=role)


async def test_task_crud_and_listing(api_client: httpx.AsyncClient, database: Database) -> None:
    ctx = await _setup(database)
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/tasks"
    created = await api_client.post(
        url,
        json={
            "title": "Collect the SF-33 signature",
            "detail": "signed by the authorised officer",
            "assignee_user_id": str(ctx["writer_id"]),
            "due_at": (DUE - timedelta(days=4)).isoformat(),
        },
        headers=_headers(ctx),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "open"
    assert body["source"] == "user"
    assert body["created_by"] == str(ctx["owner_id"])
    task_id = body["id"]

    await api_client.post(url, json={"title": "Second task"}, headers=_headers(ctx, Role.WRITER))
    listing = (await api_client.get(url, headers=_headers(ctx, Role.VIEWER))).json()
    assert listing["open_count"] == 2
    # the dated task sorts before the undated one
    assert [i["title"] for i in listing["items"]] == [
        "Collect the SF-33 signature",
        "Second task",
    ]

    done = await api_client.patch(
        f"{url}/{task_id}", json={"status": "done"}, headers=_headers(ctx, Role.WRITER)
    )
    assert done.status_code == 200
    assert done.json()["completed_by"] == str(ctx["writer_id"])
    assert done.json()["completed_at"] is not None

    only_open = (await api_client.get(url, params={"status": "open"}, headers=_headers(ctx))).json()
    assert [i["title"] for i in only_open["items"]] == ["Second task"]
    mine = (
        await api_client.get(url, params={"assignee": str(ctx["writer_id"])}, headers=_headers(ctx))
    ).json()
    assert len(mine["items"]) == 1

    reopened = await api_client.patch(
        f"{url}/{task_id}", json={"status": "open"}, headers=_headers(ctx)
    )
    assert reopened.json()["completed_at"] is None

    assert (
        await api_client.get(url, params={"status": "later"}, headers=_headers(ctx))
    ).status_code == 422
    assert (
        await api_client.delete(f"{url}/{task_id}", headers=_headers(ctx, Role.WRITER))
    ).status_code == 204
    assert (
        await api_client.patch(f"{url}/{task_id}", json={"title": "x"}, headers=_headers(ctx))
    ).status_code == 404


async def test_task_role_checks(api_client: httpx.AsyncClient, database: Database) -> None:
    ctx = await _setup(database)
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/tasks"
    for role in (Role.REVIEWER, Role.VIEWER):
        assert (
            await api_client.post(url, json={"title": "nope"}, headers=_headers(ctx, role))
        ).status_code == 403

    mine = (
        await api_client.post(
            url,
            json={"title": "Review the pricing", "assignee_user_id": str(ctx["reviewer_id"])},
            headers=_headers(ctx),
        )
    ).json()
    theirs = (
        await api_client.post(
            url,
            json={"title": "Somebody else's", "assignee_user_id": str(ctx["writer_id"])},
            headers=_headers(ctx),
        )
    ).json()

    # a reviewer may close their own task ...
    closed = await api_client.patch(
        f"{url}/{mine['id']}", json={"status": "done"}, headers=_headers(ctx, Role.REVIEWER)
    )
    assert closed.status_code == 200
    assert closed.json()["completed_by"] == str(ctx["reviewer_id"])
    # ... but not somebody else's, and never its title
    assert (
        await api_client.patch(
            f"{url}/{theirs['id']}", json={"status": "done"}, headers=_headers(ctx, Role.REVIEWER)
        )
    ).status_code == 403
    assert (
        await api_client.patch(
            f"{url}/{mine['id']}", json={"title": "renamed"}, headers=_headers(ctx, Role.REVIEWER)
        )
    ).status_code == 403
    assert (
        await api_client.delete(f"{url}/{mine['id']}", headers=_headers(ctx, Role.REVIEWER))
    ).status_code == 403
    assert (
        await api_client.post(
            url, json={"title": "t", "assignee_user_id": str(uuid.uuid4())}, headers=_headers(ctx)
        )
    ).status_code == 404


async def test_comment_crud_with_targets_and_resolution(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/comments"
    requirement_id = ctx["requirement_id"]

    on_pursuit = await api_client.post(
        url, json={"body": "Kick-off on Monday"}, headers=_headers(ctx)
    )
    assert on_pursuit.status_code == 201
    assert on_pursuit.json()["target_type"] == "pursuit"
    assert on_pursuit.json()["target_id"] is None
    assert on_pursuit.json()["author_user_id"] == str(ctx["owner_id"])

    # SPEC 3: a reviewer may comment
    on_requirement = await api_client.post(
        url,
        json={
            "body": "R-004 needs a citation",
            "target_type": "requirement",
            "target_id": str(requirement_id),
        },
        headers=_headers(ctx, Role.REVIEWER),
    )
    assert on_requirement.status_code == 201
    comment_id = on_requirement.json()["id"]

    # a viewer reads but never writes
    assert (
        await api_client.post(url, json={"body": "hi"}, headers=_headers(ctx, Role.VIEWER))
    ).status_code == 403
    assert (await api_client.get(url, headers=_headers(ctx, Role.VIEWER))).status_code == 200

    filtered = (
        await api_client.get(
            url,
            params={"target_type": "requirement", "target_id": str(requirement_id)},
            headers=_headers(ctx),
        )
    ).json()
    assert [i["id"] for i in filtered["items"]] == [comment_id]
    assert filtered["unresolved_count"] == 1
    assert (
        await api_client.get(url, params={"target_type": "nowhere"}, headers=_headers(ctx))
    ).status_code == 422
    # a target outside this pursuit is 404, not a comment pointing at nothing (M5-16)
    assert (
        await api_client.post(
            url,
            json={
                "body": "ghost",
                "target_type": "requirement",
                "target_id": str(uuid.uuid4()),
            },
            headers=_headers(ctx),
        )
    ).status_code == 404

    edited = await api_client.patch(
        f"{url}/{comment_id}",
        json={"body": "R-004 needs a page citation"},
        headers=_headers(ctx, Role.REVIEWER),
    )
    assert edited.json()["body"] == "R-004 needs a page citation"
    # only the author (or a manager) may edit the text
    assert (
        await api_client.patch(
            f"{url}/{comment_id}", json={"body": "hijacked"}, headers=_headers(ctx, Role.WRITER)
        )
    ).status_code == 403
    assert (
        await api_client.patch(
            f"{url}/{comment_id}", json={"body": "by the manager"}, headers=_headers(ctx)
        )
    ).status_code == 200

    resolved = await api_client.patch(
        f"{url}/{comment_id}", json={"resolved": True}, headers=_headers(ctx, Role.WRITER)
    )
    assert resolved.json()["resolved_at"] is not None
    assert resolved.json()["resolved_by"] == str(ctx["writer_id"])
    open_only = (
        await api_client.get(url, params={"unresolved": True}, headers=_headers(ctx))
    ).json()
    assert [i["target_type"] for i in open_only["items"]] == ["pursuit"]
    reopened = await api_client.patch(
        f"{url}/{comment_id}", json={"resolved": False}, headers=_headers(ctx)
    )
    assert reopened.json()["resolved_at"] is None

    assert (
        await api_client.delete(f"{url}/{comment_id}", headers=_headers(ctx, Role.WRITER))
    ).status_code == 403
    assert (
        await api_client.delete(f"{url}/{comment_id}", headers=_headers(ctx))
    ).status_code == 204
    assert (
        await api_client.delete(f"{url}/{uuid.uuid4()}", headers=_headers(ctx))
    ).status_code == 404


async def test_needs_input_placeholders_become_tasks_exactly_once(
    database: Database,
) -> None:
    ctx = await _setup(database)
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        assert pursuit is not None
        text = (
            "Labour: [NEEDS INPUT: labor category] at [NEEDS INPUT: price].\n"
            "Repeat of [NEEDS INPUT: labor category]."
        )
        created = await create_tasks_from_placeholders(
            session, pursuit, text, agent="pricing", ref={"artifact": "pricing_template"}
        )
        assert [task.title for task in created] == [
            "Provide: labor category",
            "Provide: price",
        ]
        assert created[0].source == "agent"
        assert created[0].assignee_user_id == ctx["owner_id"]
        assert created[0].due_at == DUE - timedelta(hours=48)
        assert created[0].ref == {
            "placeholder": "labor category",
            "agent": "pricing",
            "artifact": "pricing_template",
        }

        # a re-run of the same agent adds nothing
        assert await create_tasks_from_placeholders(session, pursuit, text, agent="pricing") == []
        assert await open_task_count(session, pursuit.id) == 2

        # a completed task stays completed rather than being re-opened
        created[1].status = "done"
        await session.flush()
        again, is_new = await create_task_from_placeholder(
            session, pursuit, "price", agent="pricing"
        )
        assert is_new is False
        assert again.id == created[1].id
        assert again.status == "done"

        # a DIFFERENT agent asking for the same thing is its own task
        other, is_new = await create_task_from_placeholder(
            session, pursuit, "price", agent="draft_technical"
        )
        assert is_new is True
        assert other.id != created[1].id
        rows = (
            (await session.execute(select(Task).where(Task.pursuit_id == pursuit.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 3


async def test_tasks_and_comments_are_tenant_scoped(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    a = await _setup(database)
    b = await _setup(database)
    await api_client.post(
        f"/api/v1/pursuits/{a['pursuit_id']}/tasks",
        json={"title": "alpha secret task"},
        headers=_headers(a),
    )
    for suffix in ("tasks", "comments"):
        response = await api_client.get(
            f"/api/v1/pursuits/{a['pursuit_id']}/{suffix}", headers=_headers(b)
        )
        assert response.status_code == 404, suffix
