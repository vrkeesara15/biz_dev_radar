"""M6-05: the WhatsApp channel end to end (eligibility from the DB) and BSP receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx
from app.core.config import Region, Settings
from app.core.db import Database
from app.models import DeliveryStatus, Notification, NotificationDelivery, User
from app.notify.core import Dispatcher, NotificationEvent, Recipient
from app.notify.registry import CHANNEL_NAMES, build_channels, whatsapp_eligibility
from app.notify.whatsapp import WhatsAppChannel
from sqlalchemy import select

from tests.factories import create_tenant_with_owner

GUPSHUP_URL = "https://api.gupshup.io/wa/api/v1/template/msg"
DUE = datetime.now(UTC) + timedelta(days=2)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "whatsapp_provider": "gupshup",
        "whatsapp_template_deadline": "bidradar_deadline_v1",
        "whatsapp_template_high_match": "bidradar_high_fit_v1",
        "gupshup_api_key": "gs-key",
        "gupshup_source_number": "918000000000",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def _tenant(
    database: Database, *, region: Region, phone: str | None, verified: bool
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session, region=region)
        owner.phone_e164 = phone
        owner.phone_verified_at = datetime.now(UTC) if verified else None
        await session.flush()
        return {"tenant_id": tenant.id, "user_id": owner.id, "email": owner.email}


def _event(ctx: dict[str, Any], event_type: str = "deadline_reminder") -> NotificationEvent:
    return NotificationEvent(
        event_type=event_type,
        tenant_id=ctx["tenant_id"],
        pursuit_id=uuid.uuid4(),
        payload={
            "title": "Supply of laptops to NIC",
            "buyer": "National Informatics Centre",
            "buyer_tz": "Asia/Kolkata",
            "due_at": DUE.isoformat(),
        },
    )


def test_whatsapp_is_a_registered_channel() -> None:
    assert "whatsapp" in CHANNEL_NAMES
    channels = build_channels(_settings(whatsapp_provider=""))
    assert isinstance(channels["whatsapp"], WhatsAppChannel)


async def test_an_indian_user_with_a_verified_number_gets_the_template(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database, region=Region.IN, phone="+919876543210", verified=True)
    settings = _settings()
    async with httpx.AsyncClient() as client, respx.mock(assert_all_called=True) as mock:
        route = mock.post(GUPSHUP_URL).mock(
            return_value=httpx.Response(200, json={"messageId": "gs-42"})
        )
        channel = WhatsAppChannel(
            settings,
            eligibility=whatsapp_eligibility(database),
            client=client,
        )
        dispatcher = Dispatcher({"whatsapp": channel}, settings)
        async with database.session(ctx["tenant_id"]) as session:
            result = await dispatcher.dispatch(
                session,
                _event(ctx),
                [Recipient(user_id=ctx["user_id"], channels=("whatsapp",), tz="Asia/Kolkata")],
            )
            assert len(result.sent) == 1
            assert result.sent[0].provider_ref == "gs-42"
    assert route.called
    assert "919876543210" in route.calls[0].request.content.decode()


async def test_a_us_tenant_and_an_unverified_number_are_skipped(
    database: Database, clean_db: Database
) -> None:
    settings = _settings()
    for region, phone, verified in (
        (Region.US, "+12025550123", True),
        (Region.IN, "+919876543210", False),
        (Region.IN, None, True),
    ):
        ctx = await _tenant(database, region=region, phone=phone, verified=verified)
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(GUPSHUP_URL)
            channel = WhatsAppChannel(settings, eligibility=whatsapp_eligibility(database))
            dispatcher = Dispatcher({"whatsapp": channel}, settings)
            async with database.session(ctx["tenant_id"]) as session:
                result = await dispatcher.dispatch(
                    session,
                    _event(ctx),
                    [Recipient(user_id=ctx["user_id"], channels=("whatsapp",))],
                )
            assert result.deliveries[0].status == DeliveryStatus.SKIPPED.value
            assert not route.called


async def test_an_unknown_user_resolves_to_nothing(database: Database, clean_db: Database) -> None:
    ctx = await _tenant(database, region=Region.IN, phone="+919876543210", verified=True)
    resolve = whatsapp_eligibility(database)
    target = await resolve(ctx["tenant_id"], uuid.uuid4())
    assert target.eligible is False


# --- delivery receipts through the webhook ---------------------------------------------------


async def _delivery(
    database: Database, tenant_id: uuid.UUID, user_id: uuid.UUID, provider_ref: str
) -> uuid.UUID:
    async with database.owner_session(tenant_id) as session:
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="deadline_reminder",
            idempotency_key=f"receipt-{uuid.uuid4()}",
            payload={},
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            tenant_id=tenant_id,
            notification_id=notification.id,
            channel="whatsapp",
            status=DeliveryStatus.QUEUED.value,
            idempotency_key=f"receipt-{uuid.uuid4()}:whatsapp",
            provider_ref=provider_ref,
        )
        session.add(delivery)
        await session.flush()
        return delivery.id


async def _status(database: Database, tenant_id: uuid.UUID, delivery_id: uuid.UUID) -> str:
    async with database.session(tenant_id) as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row is not None
        return row.status


async def test_the_receipt_webhook_moves_the_delivery_row(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database, region=Region.IN, phone="+919876543210", verified=True)
    delivery_id = await _delivery(database, ctx["tenant_id"], ctx["user_id"], "SM-100")

    delivered = await api_client.post(
        "/api/v1/webhooks/whatsapp/twilio",
        data={"MessageSid": "SM-100", "MessageStatus": "delivered"},
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json() == {
        "status": "updated",
        "provider_ref": "SM-100",
        "delivery_status": "sent",
    }
    assert await _status(database, ctx["tenant_id"], delivery_id) == "sent"

    read = await api_client.post(
        "/api/v1/webhooks/whatsapp/twilio",
        data={"MessageSid": "SM-100", "MessageStatus": "read"},
    )
    assert read.json()["delivery_status"] == "opened"
    async with database.session(ctx["tenant_id"]) as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row is not None
        assert row.opened_at is not None
        assert row.sent_at is not None

    # a late "delivered" must not demote an opened delivery
    late = await api_client.post(
        "/api/v1/webhooks/whatsapp/twilio",
        data={"MessageSid": "SM-100", "MessageStatus": "delivered"},
    )
    assert late.json()["status"] == "ignored"
    assert await _status(database, ctx["tenant_id"], delivery_id) == "opened"


async def test_a_gupshup_failure_receipt_records_the_reason(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database, region=Region.IN, phone="+919876543210", verified=True)
    delivery_id = await _delivery(database, ctx["tenant_id"], ctx["user_id"], "gs-77")
    response = await api_client.post(
        "/api/v1/webhooks/whatsapp/gupshup",
        json={"type": "failed", "payload": {"gsId": "gs-77", "payload": {"reason": "blocked"}}},
    )
    assert response.json()["delivery_status"] == "failed"
    async with database.session(ctx["tenant_id"]) as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row is not None
        assert row.last_error == "blocked"


async def test_unknown_providers_messages_and_signatures(
    api_client: httpx.AsyncClient, database: Database, app: Any
) -> None:
    assert (await api_client.post("/api/v1/webhooks/whatsapp/meta", json={})).status_code == 404
    # a message we never sent is ignored, not an error
    unknown = await api_client.post(
        "/api/v1/webhooks/whatsapp/twilio",
        data={"MessageSid": "SM-nope", "MessageStatus": "delivered"},
    )
    assert unknown.json() == {
        "status": "ignored",
        "provider_ref": "SM-nope",
        "delivery_status": None,
    }
    # an inbound reply carries no status we track
    reply = await api_client.post(
        "/api/v1/webhooks/whatsapp/gupshup",
        json={"type": "message", "payload": {"gsId": "gs-1"}},
    )
    assert reply.json()["status"] == "ignored"

    # with a secret configured, an unsigned receipt is refused
    app.state.settings = _settings(whatsapp_webhook_secret="bsp-secret")
    body = json.dumps({"type": "delivered", "payload": {"gsId": "gs-1"}}).encode()
    unsigned = await api_client.post(
        "/api/v1/webhooks/whatsapp/gupshup",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert unsigned.status_code == 400
    assert unsigned.json()["detail"] == {"error": "invalid_signature"}
    signed = await api_client.post(
        "/api/v1/webhooks/whatsapp/gupshup",
        content=body,
        headers={
            "content-type": "application/json",
            "x-gupshup-signature": hmac.new(b"bsp-secret", body, hashlib.sha256).hexdigest(),
        },
    )
    assert signed.status_code == 200


async def test_the_phone_columns_round_trip(database: Database, clean_db: Database) -> None:
    ctx = await _tenant(database, region=Region.IN, phone="+919876543210", verified=True)
    async with database.session(ctx["tenant_id"]) as session:
        user = (await session.execute(select(User).where(User.id == ctx["user_id"]))).scalar_one()
        assert user.phone_e164 == "+919876543210"
        assert user.phone_verified_at is not None
