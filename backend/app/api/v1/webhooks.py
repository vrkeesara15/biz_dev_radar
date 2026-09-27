"""Provider webhooks (SPEC 10.3 POST /webhooks/{provider}).

Providers: Stripe and Razorpay (billing), plus the WhatsApp BSPs' delivery receipts.

These routes are PUBLIC in the isolation harness sense (tests/isolation/factories.py
PUBLIC_ROUTES): there is no bearer token because the caller is Stripe / Razorpay, not a
user. Authentication is the provider's HMAC signature over the raw body; a missing or bad
signature answers 400 and nothing is read from the payload before it is verified. The
tenant comes from the verified payload (metadata / notes) or from billing_customers.
Processing is idempotent per (provider, event id), so provider retries are safe.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import SettingsDep
from app.api.v1.billing import ProvidersDep
from app.core.billing import BillingProviderName, SignatureError
from app.logging import get_logger
from app.notify import whatsapp
from app.services.billing import BillingError, BillingService
from app.services.notifications import record_whatsapp_receipt

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


class WhatsAppReceiptOut(BaseModel):
    """What the BSP callback did: updated | ignored (a status we do not track)."""

    status: str
    provider_ref: str | None = None
    delivery_status: str | None = None


@router.post("/whatsapp/{provider}", response_model=WhatsAppReceiptOut)
async def whatsapp_receipt(
    provider: str, request: Request, settings: SettingsDep
) -> WhatsAppReceiptOut:
    """BSP delivery receipts (SPEC 7): move notification_deliveries to sent/opened/failed.

    Authentication is the BSP's HMAC over the raw body where WHATSAPP_WEBHOOK_SECRET is
    configured; nothing is read from the payload before it verifies. The receipt can only
    ever update a delivery row we created ourselves, addressed by its provider_ref.
    """
    if provider not in whatsapp.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown whatsapp provider")
    body = await request.body()
    try:
        whatsapp.verify_receipt_signature(
            provider, settings, headers=dict(request.headers), body=body
        )
    except whatsapp.WhatsAppSignatureError as exc:
        log.warning("whatsapp.receipt_rejected", provider=provider, reason=str(exc))
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_signature"}
        ) from exc
    payload = await _receipt_payload(request, body)
    receipt = whatsapp.parse_receipt(provider, payload)
    if receipt is None:
        return WhatsAppReceiptOut(status="ignored")
    updated = await record_whatsapp_receipt(receipt)
    return WhatsAppReceiptOut(
        status="updated" if updated else "ignored",
        provider_ref=receipt.provider_ref,
        delivery_status=receipt.status if updated else None,
    )


async def _receipt_payload(request: Request, body: bytes) -> dict[str, Any]:
    """Twilio posts form-encoded, Gupshup posts JSON."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            parsed = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_payload"}
            ) from exc
        return parsed if isinstance(parsed, dict) else {}
    return dict(parse_qsl(body.decode("utf-8", "replace")))
