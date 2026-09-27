"""Provider webhooks (SPEC 10.3 POST /webhooks/{provider}).

These routes are PUBLIC in the isolation harness sense (tests/isolation/factories.py
PUBLIC_ROUTES): there is no bearer token because the caller is Stripe / Razorpay, not a
user. Authentication is the provider's HMAC signature over the raw body; a missing or bad
signature answers 400 and nothing is read from the payload before it is verified. The
tenant comes from the verified payload (metadata / notes) or from billing_customers.
Processing is idempotent per (provider, event id), so provider retries are safe.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.v1.billing import ProvidersDep
from app.core.billing import BillingProviderName, SignatureError
from app.logging import get_logger
from app.services.billing import BillingError, BillingService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)


class WebhookOut(BaseModel):
    status: str  # processed | duplicate | ignored
    event_id: str | None = None
    kind: str | None = None
    plan_before: str | None = None
    plan_after: str | None = None


async def _handle(request: Request, providers: Any, name: BillingProviderName) -> WebhookOut:
    provider = providers[name]
    body = await request.body()
    headers = dict(request.headers)
    try:
        payload = provider.verify_webhook(headers, body)
    except SignatureError as exc:
        log.warning("billing.webhook_rejected", provider=name.value, reason=str(exc))
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_signature"}
        ) from exc
    except BillingError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_payload"}
        ) from exc
    event = provider.parse_event(payload, headers)
    if event is None:
        return WebhookOut(status="ignored")
    payload_copy = dict(payload)
    payload_copy["_external_ids"] = dict(event.external_ids)
    outcome = await BillingService().apply_event(event, payload=payload_copy)
    return WebhookOut(
        status=outcome.status,
        event_id=outcome.event_id,
        kind=None if outcome.kind is None else outcome.kind.value,
        plan_before=None if outcome.plan_before is None else outcome.plan_before.value,
        plan_after=None if outcome.plan_after is None else outcome.plan_after.value,
    )


@router.post("/stripe", response_model=WebhookOut)
async def stripe_webhook(request: Request, providers: ProvidersDep) -> WebhookOut:
    """Stripe events (checkout.session.completed, customer.subscription.*, invoice.*)."""
    return await _handle(request, providers, BillingProviderName.STRIPE)


@router.post("/razorpay", response_model=WebhookOut)
async def razorpay_webhook(request: Request, providers: ProvidersDep) -> WebhookOut:
    """Razorpay events (subscription.*, invoice.paid, payment.failed)."""
    return await _handle(request, providers, BillingProviderName.RAZORPAY)
