"""M4-12: the in-app bell endpoints and the web-push subscription endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from app.core.db import Database
from app.core.roles import Role
from app.models import Notification, PushSubscription
from app.notify.in_app import list_notifications, mark_all_read
from app.notify.push import (
    Subscription,
    delete_push_subscription,
    load_push_subscriptions,
    save_push_subscription,
)
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


async def _tenant(database: Database) -> tuple[uuid.UUID, uuid.UUID]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        return tenant.id, user.id


async def _seed_notifications(
    database: Database, tenant_id: uuid.UUID, user_id: uuid.UUID, count: int = 3
) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    async with database.session(tenant_id) as session:
        for index in range(count):
            row = Notification(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="high_fit_match",
                version=index + 1,
                idempotency_key=f"{user_id}:high_fit_match:{uuid.uuid4()}:{index}",
                payload={
                    "title": f"Notice {index}",
                    "recipient": {"email": "hidden@example.com"},
                },
                created_at=T0 + timedelta(minutes=index),
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
    return ids


# --- bell ---------------------------------------------------------------------------------------


async def test_bell_lists_newest_first_with_the_unread_count(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id = await _tenant(database)
    ids = await _seed_notifications(database, tenant_id, user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.WRITER)

    page = (await api_client.get("/api/v1/me/notifications", headers=headers)).json()
    assert page["unread"] == 3
    assert [item["id"] for item in page["items"]] == [str(i) for i in reversed(ids)]
    assert page["items"][0]["payload"]["title"] == "Notice 2"
    # the stored recipient block is plumbing, not bell content
    assert "recipient" not in page["items"][0]["payload"]

    marked = await api_client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=headers)
    assert marked.status_code == 200 and marked.json()["id"] == str(ids[0])
    first_read_at = marked.json()["read_at"]
    # idempotent: the second click keeps the first timestamp
    again = await api_client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=headers)
    assert again.json()["read_at"] == first_read_at

    unread = (
        await api_client.get("/api/v1/me/notifications", params={"unread": 1}, headers=headers)
    ).json()
    assert unread["unread"] == 2
    assert [item["id"] for item in unread["items"]] == [str(ids[2]), str(ids[1])]

    cleared = await api_client.post("/api/v1/me/notifications/read-all", headers=headers)
    assert cleared.json() == {"marked": 2}
    after = (await api_client.get("/api/v1/me/notifications", headers=headers)).json()
    assert after["unread"] == 0 and all(item["read_at"] for item in after["items"])
    assert (await api_client.post("/api/v1/me/notifications/read-all", headers=headers)).json() == {
        "marked": 0
    }


async def test_bell_paging_and_limit(api_client: httpx.AsyncClient, database: Database) -> None:
    tenant_id, user_id = await _tenant(database)
    ids = await _seed_notifications(database, tenant_id, user_id, count=5)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.WRITER)
    first = (
        await api_client.get("/api/v1/me/notifications", params={"limit": 2}, headers=headers)
    ).json()
    assert [i["id"] for i in first["items"]] == [str(ids[4]), str(ids[3])]
    older = (
        await api_client.get(
            "/api/v1/me/notifications",
            params={"limit": 2, "before": first["items"][-1]["created_at"]},
            headers=headers,
        )
    ).json()
    assert [i["id"] for i in older["items"]] == [str(ids[2]), str(ids[1])]
    assert (
        await api_client.get("/api/v1/me/notifications", params={"limit": 0}, headers=headers)
    ).status_code == 422


async def test_bell_is_scoped_to_the_user_and_the_tenant(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_a, user_a = await _tenant(database)
    tenant_b, user_b = await _tenant(database)
    ids = await _seed_notifications(database, tenant_a, user_a, count=1)

    b_headers = auth_headers(user_id=user_b, tenant_id=tenant_b, role=Role.TENANT_OWNER)
    assert (await api_client.get("/api/v1/me/notifications", headers=b_headers)).json() == {
        "items": [],
        "unread": 0,
    }
    assert (
        await api_client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=b_headers)
    ).status_code == 404
    assert (
        await api_client.post("/api/v1/me/notifications/read-all", headers=b_headers)
    ).json() == {"marked": 0}

    # another user inside the SAME tenant cannot read someone else's bell item
    other_user = uuid.uuid4()
    same_tenant = auth_headers(user_id=other_user, tenant_id=tenant_a, role=Role.WRITER)
    assert (
        await api_client.post(f"/api/v1/me/notifications/{ids[0]}/read", headers=same_tenant)
    ).status_code == 404
    async with database.session(tenant_a) as session:
        rows, unread = await list_notifications(session, user_a)
        assert len(rows) == 1 and unread == 1
        assert await mark_all_read(session, other_user) == 0


# --- push subscriptions ---------------------------------------------------------------------------


async def test_push_subscription_round_trip(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id = await _tenant(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.WRITER)
    body = {
        "endpoint": ENDPOINT,
        "keys": {"p256dh": "BKxQ", "auth": "c2Vj"},
        "expirationTime": None,
    }
    created = await api_client.post("/api/v1/me/push-subscriptions", json=body, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["endpoint"] == ENDPOINT

    # re-posting the same endpoint refreshes the keys rather than duplicating the row
    refreshed = await api_client.post(
        "/api/v1/me/push-subscriptions",
        json={"endpoint": ENDPOINT, "keys": {"p256dh": "BKxQ2", "auth": "c2Vj2"}},
        headers=headers,
    )
    assert refreshed.json()["id"] == created.json()["id"]
    async with database.session(tenant_id) as session:
        subs = await load_push_subscriptions(session, user_id)
        assert len(subs) == 1 and subs[0].p256dh == "BKxQ2"
        row = (await session.execute(select(PushSubscription))).scalars().one()
        assert row.user_agent is not None and row.last_seen_at is not None

    removed = await api_client.request(
        "DELETE", "/api/v1/me/push-subscriptions", json={"endpoint": ENDPOINT}, headers=headers
    )
    assert removed.status_code == 204
    assert (
        await api_client.request(
            "DELETE",
            "/api/v1/me/push-subscriptions",
            json={"endpoint": ENDPOINT},
            headers=headers,
        )
    ).status_code == 404
    async with database.session(tenant_id) as session:
        assert await load_push_subscriptions(session, user_id) == []


async def test_push_subscriptions_are_tenant_and_user_scoped(database: Database) -> None:
    tenant_a, user_a = await _tenant(database)
    tenant_b, user_b = await _tenant(database)
    async with database.session(tenant_a) as session:
        await save_push_subscription(
            session,
            tenant_id=tenant_a,
            user_id=user_a,
            subscription=Subscription(ENDPOINT, "p", "a"),
            user_agent="Firefox",
            now=T0,
        )
    async with database.session(tenant_b) as session:
        assert await load_push_subscriptions(session, user_a) == []
        assert await load_push_subscriptions(session, user_b) == []
        assert await delete_push_subscription(session, user_a, ENDPOINT) is False
    async with database.session(tenant_a) as session:
        assert len(await load_push_subscriptions(session, user_a)) == 1
        assert await delete_push_subscription(session, user_a, "https://other/endpoint") is False
        assert await delete_push_subscription(session, user_a, ENDPOINT) is True


async def test_push_subscription_body_is_validated(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id = await _tenant(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.WRITER)
    for bad in ({"endpoint": ENDPOINT}, {"endpoint": "", "keys": {"p256dh": "a", "auth": "b"}}):
        assert (
            await api_client.post("/api/v1/me/push-subscriptions", json=bad, headers=headers)
        ).status_code == 422
