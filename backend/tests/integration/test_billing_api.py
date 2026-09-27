"""M7-04: checkout, provider webhooks and immediate plan enforcement.

Stripe / Razorpay HTTP is mocked with respx (no network); webhook bodies are signed with
the test endpoint secrets so the real signature verification runs on every call.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import respx
from app.core.billing import razorpay_signature, stripe_signature_header
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.plan import Plan
from app.core.roles import Role
from app.main import create_app
from app.models import AuditLog, BillingCustomer, BillingEventRecord, Tenant, UsageLedger
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

STRIPE_API = "https://api.stripe.com/v1"
RAZORPAY_API = "https://api.razorpay.com/v1"
STRIPE_WEBHOOK_SECRET = "whsec_stripe_test"
RAZORPAY_WEBHOOK_SECRET = "whsec_razorpay_test"
SUPPLIER_GSTIN = "29AAGCB7383J1ZL"
CUSTOMER_GSTIN = "29AABCU9603R1ZM"

BILLING_SETTINGS: dict[str, Any] = {
    "stripe_secret_key": "sk_test_123",
    "stripe_webhook_secret": STRIPE_WEBHOOK_SECRET,
    "stripe_price_ids": {"pro": "price_pro", "enterprise": "price_ent"},
    "razorpay_key_id": "rzp_test_123",
    "razorpay_key_secret": "rzp_secret",
    "razorpay_webhook_secret": RAZORPAY_WEBHOOK_SECRET,
    "razorpay_plan_ids": {"pro": "plan_pro", "enterprise": "plan_ent"},
    "billing_gstin": SUPPLIER_GSTIN,
    "billing_gst_rate_pct": 18,
}


@pytest.fixture()
def billing_settings(settings: Settings) -> Settings:
    return settings.model_copy(update=BILLING_SETTINGS)


@pytest.fixture()
def billing_app(billing_settings: Settings, fake_embeddings: Any) -> FastAPI:
    """A second app with billing credentials; `app`/`api_client` stay key-less."""
    application = create_app(billing_settings)
    application.state.embeddings = fake_embeddings
    return application


@pytest.fixture()
async def billing_client(billing_app: FastAPI, clean_db: Any) -> Any:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=billing_app, client=("203.0.113.10", 51000)),
        base_url="http://test",
    ) as client:
        yield client


class Seed:
    def __init__(self, tenant_id: uuid.UUID, user_id: uuid.UUID, email: str) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.email = email

    def headers(self, role: Role = Role.TENANT_OWNER) -> dict[str, str]:
        return auth_headers(
            user_id=self.user_id, tenant_id=self.tenant_id, role=role, email=self.email
        )


async def seed_tenant(database: Database, **overrides: Any) -> Seed:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return Seed(tenant.id, user.id, user.email)


async def plan_of(database: Database, tenant_id: uuid.UUID) -> Plan:
    async with database.owner_session() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        return Plan(tenant.plan)


async def rows(database: Database, model: Any, tenant_id: uuid.UUID) -> list[Any]:
    async with database.owner_session() as session:
        result = await session.execute(select(model).where(model.tenant_id == tenant_id))
        return list(result.scalars())


# --- checkout -------------------------------------------------------------------------------


def mock_stripe_checkout() -> tuple[respx.Route, respx.Route]:
    customers = respx.post(f"{STRIPE_API}/customers").mock(
        return_value=httpx.Response(200, json={"id": "cus_1"})
    )
    sessions = respx.post(f"{STRIPE_API}/checkout/sessions").mock(
        return_value=httpx.Response(
            200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"}
        )
    )
    return customers, sessions


@respx.mock
async def test_us_tenant_checkout_uses_stripe(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US, plan=Plan.FREE)
    customers, sessions = mock_stripe_checkout()

    r = await billing_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "pro",
            "success_url": "https://app.example/billing?ok=1",
            "cancel_url": "https://app.example/billing?cancelled=1",
        },
        headers=seed.headers(),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "stripe"
    assert body["currency"] == "USD"
    assert body["url"] == "https://checkout.stripe.com/c/pay/cs_1"
    assert body["session_id"] == "cs_1"

    assert customers.called and sessions.called
    form = dict(httpx.QueryParams(sessions.calls.last.request.content.decode()))
    assert form["mode"] == "subscription"
    assert form["customer"] == "cus_1"
    assert form["line_items[0][price]"] == "price_pro"
    assert form["client_reference_id"] == str(seed.tenant_id)
    assert form["metadata[tenant_id]"] == str(seed.tenant_id)
    assert form["subscription_data[metadata][plan]"] == "pro"

    customer_rows = await rows(database, BillingCustomer, seed.tenant_id)
    assert [(c.provider, c.customer_id, c.status) for c in customer_rows] == [
        ("stripe", "cus_1", "none")
    ]
    # checkout never changes the plan on its own; only the webhook does
    assert await plan_of(database, seed.tenant_id) is Plan.FREE

    async with database.owner_session() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "billing.checkout")  # type: ignore[arg-type]
            )
        ).scalars()
        actions = [a.meta for a in audit]
    assert actions and actions[0]["plan"] == "pro" and actions[0]["provider"] == "stripe"


@respx.mock
async def test_second_checkout_reuses_the_stored_customer(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    customers, _ = mock_stripe_checkout()
    payload = {
        "plan": "pro",
        "success_url": "https://app.example/ok",
        "cancel_url": "https://app.example/no",
    }
    first = await billing_client.post(
        "/api/v1/billing/checkout", json=payload, headers=seed.headers()
    )
    second = await billing_client.post(
        "/api/v1/billing/checkout", json=payload, headers=seed.headers()
    )
    assert first.status_code == second.status_code == 201
    assert customers.call_count == 1
    assert len(await rows(database, BillingCustomer, seed.tenant_id)) == 1


@respx.mock
async def test_in_tenant_checkout_uses_razorpay_with_gst_notes(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.IN, data_residency=Region.IN, plan=Plan.FREE)
    customers = respx.post(f"{RAZORPAY_API}/customers").mock(
        return_value=httpx.Response(200, json={"id": "cust_rzp"})
    )
    subs = respx.post(f"{RAZORPAY_API}/subscriptions").mock(
        return_value=httpx.Response(
            200, json={"id": "sub_rzp", "short_url": "https://rzp.io/i/sub_rzp"}
        )
    )

    r = await billing_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "pro",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/no",
            "gst": {
                "gstin": CUSTOMER_GSTIN,
                "place_of_supply": "29",
                "legal_name": "Bharat Infra Pvt Ltd",
            },
        },
        headers=seed.headers(),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "razorpay"
    assert body["currency"] == "INR"
    assert body["url"] == "https://rzp.io/i/sub_rzp"

    assert customers.called
    sub_body = subs.calls.last.request.read()
    import json as _json

    sent = _json.loads(sub_body)
    assert sent["plan_id"] == "plan_pro"
    assert sent["customer_id"] == "cust_rzp"
    assert sent["notes"]["tenant_id"] == str(seed.tenant_id)
    assert sent["notes"]["plan"] == "pro"
    assert sent["notes"]["gstin"] == CUSTOMER_GSTIN
    assert sent["notes"]["place_of_supply"] == "29"

    customer_rows = await rows(database, BillingCustomer, seed.tenant_id)
    assert customer_rows[0].gst_details["gstin"] == CUSTOMER_GSTIN


@respx.mock
async def test_gst_details_are_rejected_for_us_tenants(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    mock_stripe_checkout()
    r = await billing_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "pro",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/no",
            "gst": {"gstin": CUSTOMER_GSTIN},
        },
        headers=seed.headers(),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "region_mismatch"


@respx.mock
async def test_checkout_requires_the_tenant_owner(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    mock_stripe_checkout()
    for role in (Role.BID_MANAGER, Role.WRITER, Role.VIEWER):
        r = await billing_client.post(
            "/api/v1/billing/checkout",
            json={
                "plan": "pro",
                "success_url": "https://app.example/ok",
                "cancel_url": "https://app.example/no",
            },
            headers=seed.headers(role),
        )
        assert r.status_code == 403, (role, r.text)


@respx.mock
async def test_checkout_rejects_the_free_plan_and_non_https_return_urls(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    mock_stripe_checkout()
    free = await billing_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "free",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/no",
        },
        headers=seed.headers(),
    )
    assert free.status_code == 422
    insecure = await billing_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "pro",
            "success_url": "http://app.example/ok",
            "cancel_url": "https://app.example/no",
        },
        headers=seed.headers(),
    )
    assert insecure.status_code == 422


async def test_checkout_without_credentials_is_service_unavailable(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    """The default app has no Stripe key: the route says so instead of calling out."""
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    r = await api_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "pro",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/no",
        },
        headers=seed.headers(),
    )
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "billing_not_configured"


@respx.mock
async def test_provider_failure_is_a_bad_gateway(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    respx.post(f"{STRIPE_API}/customers").mock(
        return_value=httpx.Response(402, json={"error": {"message": "card declined"}})
    )
    r = await billing_client.post(
        "/api/v1/billing/checkout",
        json={
            "plan": "pro",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/no",
        },
        headers=seed.headers(),
    )
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "billing_provider_error"


# --- webhooks -------------------------------------------------------------------------------


def stripe_body(event_type: str, obj: dict[str, Any], event_id: str = "evt_1") -> bytes:
    import json as _json

    return _json.dumps({"id": event_id, "type": event_type, "data": {"object": obj}}).encode()


def stripe_headers(body: bytes, secret: str = STRIPE_WEBHOOK_SECRET) -> dict[str, str]:
    return {
        "Stripe-Signature": stripe_signature_header(body, secret),
        "Content-Type": "application/json",
    }


def razorpay_body(event: str, entities: dict[str, Any]) -> bytes:
    import json as _json

    return _json.dumps(
        {"event": event, "payload": {k: {"entity": v} for k, v in entities.items()}}
    ).encode()


def razorpay_headers(
    body: bytes, event_id: str = "rzp_evt_1", secret: str = RAZORPAY_WEBHOOK_SECRET
) -> dict[str, str]:
    return {
        "X-Razorpay-Signature": razorpay_signature(body, secret),
        "X-Razorpay-Event-Id": event_id,
        "Content-Type": "application/json",
    }


def checkout_completed(tenant_id: uuid.UUID, plan: str = "pro") -> bytes:
    return stripe_body(
        "checkout.session.completed",
        {
            "id": "cs_1",
            "customer": "cus_1",
            "subscription": "sub_1",
            "client_reference_id": str(tenant_id),
            "metadata": {"tenant_id": str(tenant_id), "plan": plan},
            "amount_total": 9900,
            "currency": "usd",
            "status": "complete",
        },
    )


async def test_stripe_webhook_rejects_a_bad_signature(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US, plan=Plan.FREE)
    body = checkout_completed(seed.tenant_id)

    wrong = await billing_client.post(
        "/api/v1/webhooks/stripe",
        content=body,
        headers=stripe_headers(body, "whsec_someone_else"),
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"]["error"] == "invalid_signature"

    missing = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body, headers={"Content-Type": "application/json"}
    )
    assert missing.status_code == 400

    tampered_headers = stripe_headers(body)
    tampered = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body + b" ", headers=tampered_headers
    )
    assert tampered.status_code == 400

    # nothing was applied
    assert await plan_of(database, seed.tenant_id) is Plan.FREE
    assert await rows(database, BillingEventRecord, seed.tenant_id) == []


async def test_razorpay_webhook_rejects_a_bad_signature(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.IN, data_residency=Region.IN, plan=Plan.FREE)
    body = razorpay_body(
        "subscription.authenticated",
        {
            "subscription": {
                "id": "sub_rzp",
                "plan_id": "plan_pro",
                "notes": {"tenant_id": str(seed.tenant_id)},
            }
        },
    )
    r = await billing_client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers=razorpay_headers(body, secret="whsec_wrong"),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_signature"
    assert await plan_of(database, seed.tenant_id) is Plan.FREE


async def test_webhooks_need_no_bearer_token(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    """Webhooks authenticate by signature, not by tenant: an unsigned call is 400, not 401."""
    body = checkout_completed(uuid.uuid4())
    r = await billing_client.post("/api/v1/webhooks/stripe", content=body)
    assert r.status_code == 400


async def test_stripe_checkout_webhook_upgrades_the_plan_and_is_idempotent(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US, plan=Plan.FREE)
    body = checkout_completed(seed.tenant_id)

    first = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "status": "processed",
        "event_id": "evt_1",
        "kind": "checkout_completed",
        "plan_before": "free",
        "plan_after": "pro",
    }
    assert await plan_of(database, seed.tenant_id) is Plan.PRO

    ledger = [
        row
        for row in await rows(database, UsageLedger, seed.tenant_id)
        if row.metric == "plan_change"
    ]
    assert len(ledger) == 1
    assert ledger[0].ref.startswith("stripe:evt_1:free->pro")

    customer = (await rows(database, BillingCustomer, seed.tenant_id))[0]
    assert customer.customer_id == "cus_1"
    assert customer.subscription_id == "sub_1"
    assert customer.plan is Plan.PRO

    # the webhook has no user, so the service writes the audit row itself
    audit_rows = [
        row
        for row in await rows(database, AuditLog, seed.tenant_id)
        if row.action == "billing.webhook"
    ]
    assert len(audit_rows) == 1
    assert audit_rows[0].user_id is None
    assert audit_rows[0].object_type == "tenant"
    assert audit_rows[0].object_id == str(seed.tenant_id)
    assert audit_rows[0].meta == {
        "provider": "stripe",
        "kind": "checkout_completed",
        "event_id": "evt_1",
        "plan_before": "free",
        "plan_after": "pro",
    }

    # a provider retry of the same event changes nothing
    replay = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"
    assert len(await rows(database, BillingEventRecord, seed.tenant_id)) == 1
    assert (
        len(
            [
                r
                for r in await rows(database, AuditLog, seed.tenant_id)
                if r.action == "billing.webhook"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                r
                for r in await rows(database, UsageLedger, seed.tenant_id)
                if r.metric == "plan_change"
            ]
        )
        == 1
    )


async def test_subscription_cancelled_returns_the_tenant_to_free(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US, plan=Plan.FREE)
    up = checkout_completed(seed.tenant_id)
    await billing_client.post("/api/v1/webhooks/stripe", content=up, headers=stripe_headers(up))
    assert await plan_of(database, seed.tenant_id) is Plan.PRO

    down = stripe_body(
        "customer.subscription.deleted",
        {"id": "sub_1", "customer": "cus_1", "metadata": {"tenant_id": str(seed.tenant_id)}},
        event_id="evt_cancel",
    )
    r = await billing_client.post(
        "/api/v1/webhooks/stripe", content=down, headers=stripe_headers(down)
    )
    assert r.status_code == 200
    assert r.json()["plan_after"] == "free"
    assert await plan_of(database, seed.tenant_id) is Plan.FREE
    customer = (await rows(database, BillingCustomer, seed.tenant_id))[0]
    assert customer.status == "canceled"


async def test_webhook_for_an_unknown_tenant_is_ignored(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    body = checkout_completed(uuid.uuid4())
    r = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


async def test_unhandled_event_types_are_ignored(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    body = stripe_body("customer.created", {"id": "cus_1"})
    r = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


async def test_razorpay_invoice_paid_records_the_gst_split(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.IN, data_residency=Region.IN, plan=Plan.FREE)
    notes = {
        "tenant_id": str(seed.tenant_id),
        "plan": "pro",
        "gstin": CUSTOMER_GSTIN,
        "place_of_supply": "29",
    }
    auth = razorpay_body(
        "subscription.authenticated",
        {
            "subscription": {
                "id": "sub_rzp",
                "customer_id": "cust_rzp",
                "plan_id": "plan_pro",
                "status": "authenticated",
                "current_end": 1_800_000_000,
                "notes": notes,
            }
        },
    )
    r = await billing_client.post(
        "/api/v1/webhooks/razorpay", content=auth, headers=razorpay_headers(auth)
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan_after"] == "pro"
    assert await plan_of(database, seed.tenant_id) is Plan.PRO

    invoice = razorpay_body(
        "invoice.paid",
        {
            "invoice": {
                "id": "inv_rzp",
                "subscription_id": "sub_rzp",
                "customer_id": "cust_rzp",
                "invoice_number": "BR-IN-0001",
                "short_url": "https://rzp.io/i/inv",
                "amount_paid": 118_000,
                "status": "paid",
                "notes": notes,
            }
        },
    )
    r2 = await billing_client.post(
        "/api/v1/webhooks/razorpay",
        content=invoice,
        headers=razorpay_headers(invoice, event_id="rzp_evt_2"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["kind"] == "invoice_paid"

    events = {e.kind: e for e in await rows(database, BillingEventRecord, seed.tenant_id)}
    paid = events["invoice_paid"]
    assert paid.amount == 118_000
    assert paid.currency == "INR"
    assert paid.gst["supplier_gstin"] == SUPPLIER_GSTIN
    assert paid.gst["recipient_gstin"] == CUSTOMER_GSTIN
    assert paid.gst["taxable_paise"] == 100_000
    assert paid.gst["cgst_paise"] == paid.gst["sgst_paise"] == 9_000
    assert paid.gst["tax_rate_pct"] == 18
    assert paid.gst["invoice_number"] == "BR-IN-0001"

    # and the settings screen shows it
    view = await billing_client.get("/api/v1/billing", headers=seed.headers())
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["plan"] == "pro"
    assert body["provider"] == "razorpay"
    assert body["currency"] == "INR"
    assert body["subscription_id"] == "sub_rzp"
    assert body["gst_details"]["gstin"] == CUSTOMER_GSTIN
    assert body["last_invoice"]["number"] == "BR-IN-0001"
    assert body["last_invoice"]["amount"] == 118_000
    assert body["last_invoice"]["gst"]["igst_paise"] == 0
    assert body["next_invoice_at"] is not None


async def test_billing_events_are_append_only_for_the_app_role(database: Database) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    async with database.owner_session() as session:
        session.add(
            BillingEventRecord(
                tenant_id=seed.tenant_id,
                provider="stripe",
                event_id="evt_append_only",
                kind="invoice_paid",
            )
        )
    async with database.session(seed.tenant_id) as session:
        with pytest.raises(ProgrammingError):
            await session.execute(
                BillingEventRecord.__table__.delete().where(
                    BillingEventRecord.event_id == "evt_append_only"
                )
            )


# --- plan enforcement -------------------------------------------------------------------------


async def test_plan_change_is_enforced_immediately_by_plan_service(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    """A free tenant is blocked at its second profile; after the webhook it is allowed."""
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US, plan=Plan.FREE)
    headers = seed.headers()

    first = await billing_client.post(
        "/api/v1/profiles", json={"region": "us", "legal_name": "Free One LLC"}, headers=headers
    )
    assert first.status_code == 201, first.text

    blocked = await billing_client.post(
        "/api/v1/profiles", json={"region": "us", "legal_name": "Free Two LLC"}, headers=headers
    )
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["detail"]["limit"] == "profiles"
    assert blocked.json()["detail"]["plan"] == "free"

    body = checkout_completed(seed.tenant_id)
    hook = await billing_client.post(
        "/api/v1/webhooks/stripe", content=body, headers=stripe_headers(body)
    )
    assert hook.status_code == 200 and hook.json()["plan_after"] == "pro"

    allowed = await billing_client.post(
        "/api/v1/profiles", json={"region": "us", "legal_name": "Pro Two LLC"}, headers=headers
    )
    assert allowed.status_code == 201, allowed.text

    # ... and the pro limit of 3 still bites at the fourth
    third = await billing_client.post(
        "/api/v1/profiles", json={"region": "us", "legal_name": "Pro Three LLC"}, headers=headers
    )
    assert third.status_code == 201, third.text
    fourth = await billing_client.post(
        "/api/v1/profiles", json={"region": "us", "legal_name": "Pro Four LLC"}, headers=headers
    )
    assert fourth.status_code == 402


async def test_billing_view_lists_usage_against_plan_limits(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US, plan=Plan.FREE)
    headers = seed.headers()
    await billing_client.post(
        "/api/v1/profiles", json={"region": "us", "legal_name": "Free One LLC"}, headers=headers
    )
    r = await billing_client.get("/api/v1/billing", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == "free"
    assert body["provider"] == "stripe"
    assert body["status"] == "none"
    assert body["last_invoice"] is None
    assert body["next_invoice_at"] is None
    limits = {row["resource"]: row for row in body["limits"]}
    assert limits["profiles"] == {"resource": "profiles", "limit": 1, "used": 1, "remaining": 0}
    assert limits["agent_drafts_per_month"]["limit"] == 0
    # M5-02 added the monthly LLM budget to plan_limits: the free plan gets none
    assert limits["agent_budget_usd_month"]["limit"] == 0
    assert set(limits) == {
        "profiles",
        "source_regions",
        "instant_alerts",
        "agent_drafts_per_month",
        "agent_budget_usd_month",
    }


async def test_billing_view_is_readable_by_every_tenant_role(
    billing_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.US, data_residency=Region.US)
    for role in (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER):
        r = await billing_client.get("/api/v1/billing", headers=seed.headers(role))
        assert r.status_code == 200, (role, r.text)
