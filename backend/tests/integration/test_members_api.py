"""M7-15: tenant member management (SPEC 3, 10.4 screen 8) and the push config route.

    GET    /api/v1/tenant/members                    any member of the tenant
    POST   /api/v1/tenant/members/invite             tenant_owner
    PATCH  /api/v1/tenant/members/{membership_id}    tenant_owner
    DELETE /api/v1/tenant/members/{membership_id}    tenant_owner
    GET    /api/v1/me/push-config                    the VAPID public key, or 404

The invite emits a `member.invited` event through the notification Dispatcher; the tests
swap the app's dispatcher for a recording one so nothing leaves the process.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.api.v1.members import MEMBER_INVITED
from app.core.config import Settings
from app.core.db import Database
from app.core.roles import Role
from app.models import AuditLog, Membership, Notification, User
from app.notify.core import Dispatcher, SendResult
from fastapi import FastAPI
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user


class RecordingChannel:
    """A Channel that never sends: it keeps (event_type, email) for the assertions."""

    name = "email"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None]] = []

    async def send(self, delivery: Any, notification: Notification, recipient: Any) -> SendResult:
        self.sent.append((notification.event_type, recipient.email))
        return SendResult.sent("recorded")


@pytest.fixture()
def channel(app: FastAPI, settings: Settings) -> RecordingChannel:
    recorder = RecordingChannel()
    app.state.dispatcher = Dispatcher({"email": recorder}, settings)
    return recorder


async def _tenant(database: Database, **overrides: Any) -> tuple[uuid.UUID, uuid.UUID, str]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id, user.id, user.email


async def _member(
    database: Database, tenant_id: uuid.UUID, role: Role = Role.WRITER
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A second member of `tenant_id`; returns (membership_id, user_id, email)."""
    async with database.owner_session() as session:
        user = make_user()
        session.add(user)
        await session.flush()
        membership = Membership(tenant_id=tenant_id, user_id=user.id, role=role)
        session.add(membership)
        await session.flush()
        return membership.id, user.id, user.email


def _headers(tenant_id: uuid.UUID, user_id: uuid.UUID, role: Role, email: str) -> dict[str, str]:
    return auth_headers(user_id=user_id, tenant_id=tenant_id, role=role, email=email)


async def test_list_members_is_open_to_every_member(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, owner_id, owner_email = await _tenant(database)
    _, writer_id, writer_email = await _member(database, tid)
    # the owner's own row is provisioned just in time by /me (M0-07)
    await api_client.get(
        "/api/v1/me", headers=_headers(tid, owner_id, Role.TENANT_OWNER, owner_email)
    )

    r = await api_client.get(
        "/api/v1/tenant/members", headers=_headers(tid, writer_id, Role.WRITER, writer_email)
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert {row["email"] for row in rows} == {owner_email, writer_email}
    by_email = {row["email"]: row for row in rows}
    assert by_email[owner_email]["role"] == "tenant_owner"
    assert by_email[writer_email]["role"] == "writer"
    assert by_email[writer_email]["user_id"] == str(writer_id)
    assert uuid.UUID(by_email[writer_email]["id"])  # the membership id, used by PATCH/DELETE
    assert by_email[writer_email]["joined_at"]

    # another tenant sees only its own members
    other_tid, other_uid, other_email = await _tenant(database)
    r = await api_client.get(
        "/api/v1/tenant/members",
        headers=_headers(other_tid, other_uid, Role.TENANT_OWNER, other_email),
    )
    assert r.status_code == 200, r.text
    assert [row["email"] for row in r.json()] == [other_email]


async def test_invite_creates_the_user_membership_and_email_event(
    api_client: httpx.AsyncClient, database: Database, channel: RecordingChannel
) -> None:
    tid, owner_id, owner_email = await _tenant(database)
    headers = _headers(tid, owner_id, Role.TENANT_OWNER, owner_email)
    r = await api_client.post(
        "/api/v1/tenant/members/invite",
        json={"email": "New.Person@Example.com", "role": "bid_manager"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new.person@example.com" and body["role"] == "bid_manager"

    async with database.owner_session() as session:
        membership = await session.get(Membership, uuid.UUID(body["id"]))
        assert membership is not None and membership.tenant_id == tid
        assert membership.role is Role.BID_MANAGER
        user = await session.get(User, uuid.UUID(body["user_id"]))
        assert user is not None and user.email == "new.person@example.com"
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.at))).scalars().all()
        assert [row.action for row in rows] == ["member.invite"]
        assert rows[0].object_id == body["id"] and rows[0].user_id == owner_id
        notifications = (await session.execute(select(Notification))).scalars().all()
        assert [n.event_type for n in notifications] == [MEMBER_INVITED]

    assert channel.sent == [(MEMBER_INVITED, "new.person@example.com")]

    # inviting the same address again is a conflict, not a second membership
    r = await api_client.post(
        "/api/v1/tenant/members/invite",
        json={"email": "new.person@example.com", "role": "viewer"},
        headers=headers,
    )
    assert r.status_code == 409, r.text


async def test_invite_attaches_an_existing_user_to_this_tenant(
    api_client: httpx.AsyncClient, database: Database, channel: RecordingChannel
) -> None:
    first_tid, first_uid, first_email = await _tenant(database)
    tid, owner_id, owner_email = await _tenant(database)
    r = await api_client.post(
        "/api/v1/tenant/members/invite",
        json={"email": first_email, "role": "reviewer"},
        headers=_headers(tid, owner_id, Role.TENANT_OWNER, owner_email),
    )
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == str(first_uid), "the existing users row is reused"
    async with database.owner_session() as session:
        rows = (
            (await session.execute(select(Membership).where(Membership.user_id == first_uid)))
            .scalars()
            .all()
        )
        assert {row.tenant_id for row in rows} == {first_tid, tid}


async def test_writes_are_tenant_owner_only(
    api_client: httpx.AsyncClient, database: Database, channel: RecordingChannel
) -> None:
    tid, _, _ = await _tenant(database)
    membership_id, writer_id, writer_email = await _member(database, tid)
    writer = _headers(tid, writer_id, Role.WRITER, writer_email)
    r = await api_client.post(
        "/api/v1/tenant/members/invite",
        json={"email": "x@example.com", "role": "viewer"},
        headers=writer,
    )
    assert r.status_code == 403, r.text
    r = await api_client.patch(
        f"/api/v1/tenant/members/{membership_id}", json={"role": "viewer"}, headers=writer
    )
    assert r.status_code == 403
    r = await api_client.delete(f"/api/v1/tenant/members/{membership_id}", headers=writer)
    assert r.status_code == 403
    assert channel.sent == []


async def test_invite_rejects_platform_admin_and_a_bad_address(
    api_client: httpx.AsyncClient, database: Database, channel: RecordingChannel
) -> None:
    tid, owner_id, owner_email = await _tenant(database)
    headers = _headers(tid, owner_id, Role.TENANT_OWNER, owner_email)
    for body in (
        {"email": "admin@example.com", "role": "platform_admin"},
        {"email": "not-an-address", "role": "viewer"},
        {"email": "ok@example.com", "role": "emperor"},
        {"email": "ok@example.com", "role": "viewer", "surprise": 1},
    ):
        r = await api_client.post("/api/v1/tenant/members/invite", json=body, headers=headers)
        assert r.status_code == 422, (body, r.text)


async def test_role_change_and_the_last_owner_guard(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, owner_id, owner_email = await _tenant(database)
    headers = _headers(tid, owner_id, Role.TENANT_OWNER, owner_email)
    membership_id, _, writer_email = await _member(database, tid)
    owner_membership = await _owner_membership(database, tid, owner_id)

    r = await api_client.patch(
        f"/api/v1/tenant/members/{membership_id}", json={"role": "bid_manager"}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "bid_manager" and r.json()["email"] == writer_email

    # the only owner may not demote themselves, nor be removed
    r = await api_client.patch(
        f"/api/v1/tenant/members/{owner_membership}", json={"role": "viewer"}, headers=headers
    )
    assert r.status_code == 409, r.text
    r = await api_client.delete(f"/api/v1/tenant/members/{owner_membership}", headers=headers)
    assert r.status_code == 409

    # with a second owner both are allowed again
    r = await api_client.patch(
        f"/api/v1/tenant/members/{membership_id}", json={"role": "tenant_owner"}, headers=headers
    )
    assert r.status_code == 200, r.text
    r = await api_client.delete(f"/api/v1/tenant/members/{owner_membership}", headers=headers)
    assert r.status_code == 204, r.text

    async with database.owner_session() as session:
        rows = (
            (await session.execute(select(Membership).where(Membership.tenant_id == tid)))
            .scalars()
            .all()
        )
        assert [row.role for row in rows] == [Role.TENANT_OWNER]
        actions = sorted(
            row.action for row in (await session.execute(select(AuditLog))).scalars().all()
        )
    # every write is audited, the two refused ones included
    assert actions == [
        "member.remove",
        "member.remove",
        "member.role_change",
        "member.role_change",
        "member.role_change",
    ]


async def test_unknown_membership_and_a_foreign_one_are_404(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, owner_id, owner_email = await _tenant(database)
    other_tid, _, _ = await _tenant(database)
    foreign_membership, _, _ = await _member(database, other_tid)
    headers = _headers(tid, owner_id, Role.TENANT_OWNER, owner_email)
    for membership_id in (uuid.uuid4(), foreign_membership):
        r = await api_client.patch(
            f"/api/v1/tenant/members/{membership_id}", json={"role": "viewer"}, headers=headers
        )
        assert r.status_code == 404, r.text
        r = await api_client.delete(f"/api/v1/tenant/members/{membership_id}", headers=headers)
        assert r.status_code == 404


async def test_a_member_may_remove_themselves_when_they_are_not_the_last_owner(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, _, _ = await _tenant(database)
    second, second_uid, second_email = await _member(database, tid, role=Role.TENANT_OWNER)
    r = await api_client.delete(
        f"/api/v1/tenant/members/{second}",
        headers=_headers(tid, second_uid, Role.TENANT_OWNER, second_email),
    )
    assert r.status_code == 204, r.text


async def _owner_membership(
    database: Database, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    async with database.owner_session() as session:
        row = (
            await session.execute(
                select(Membership).where(
                    Membership.tenant_id == tenant_id, Membership.user_id == user_id
                )
            )
        ).scalar_one()
        return row.id


async def test_push_config_serves_the_vapid_public_key(
    api_client: httpx.AsyncClient, database: Database, settings: Settings, fake_embeddings: Any
) -> None:
    from app.main import create_app

    tid, uid, email = await _tenant(database)
    headers = _headers(tid, uid, Role.VIEWER, email)
    r = await api_client.get("/api/v1/me/push-config", headers=headers)
    assert r.status_code == 404, r.text  # no key configured in the test settings

    configured = create_app(settings.model_copy(update={"vapid_public_key": "  BPublicKey123  "}))
    configured.state.embeddings = fake_embeddings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=configured), base_url="http://test"
    ) as client:
        r = await client.get("/api/v1/me/push-config", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"vapid_public_key": "BPublicKey123"}

    r = await api_client.get("/api/v1/me/push-config")
    assert r.status_code == 401
