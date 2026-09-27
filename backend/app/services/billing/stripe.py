"""Stripe provider (US tenants, USD). Raw REST via httpx: no SDK, so tests mock with respx.

Checkout = Stripe Checkout Session in subscription mode for the plan's price id; the
tenant id travels as client_reference_id and in the session + subscription metadata so
every later webhook (subscription.*, invoice.*) can be attributed without a lookup.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from app.core.billing import (
    STRIPE_SIGNATURE_HEADER,
    BillingEvent,
    BillingProviderName,
    parse_stripe_event,
    verify_stripe_signature,
)
from app.core.config import Settings
from app.core.plan import Plan
from app.services.billing.base import BillingError, CheckoutSession, CustomerRef, header


class StripeProvider:
    name = BillingProviderName.STRIPE

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str,
        price_ids: dict[str, str],
        api_url: str = "https://api.stripe.com/v1",
        timeout: float = 20.0,
    ) -> None:
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret
        self.price_ids = dict(price_ids)
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> StripeProvider:
        return cls(
            secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret,
            price_ids=settings.stripe_price_ids,
            api_url=settings.stripe_api_url,
        )

    @property
    def configured(self) -> bool:
        return bool(self.secret_key and self.price_ids)

    async def _post(self, path: str, form: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.api_url}/{path.lstrip('/')}",
                    data=form,
                    auth=(self.secret_key, ""),
                )
            except httpx.HTTPError as exc:
                raise BillingError(f"stripe unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise BillingError(f"stripe {path} -> {response.status_code}: {response.text[:300]}")
        data: dict[str, Any] = response.json()
        return data

    async def create_customer(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        email: str,
        gst_details: dict[str, Any] | None = None,
    ) -> CustomerRef:
        data = await self._post(
            "customers", {"name": name, "email": email, "metadata[tenant_id]": str(tenant_id)}
        )
        return CustomerRef(id=str(data["id"]), gst_details={})

    async def create_checkout(
        self,
        *,
        customer_id: str,
        tenant_id: uuid.UUID,
        plan: Plan,
        success_url: str,
        cancel_url: str,
        gst_details: dict[str, Any] | None = None,
    ) -> CheckoutSession:
        price = self.price_ids.get(plan.value)
        if not price:
            raise BillingError(f"no Stripe price configured for plan {plan.value}")
        data = await self._post(
            "checkout/sessions",
            {
                "mode": "subscription",
                "customer": customer_id,
                "client_reference_id": str(tenant_id),
                "line_items[0][price]": price,
                "line_items[0][quantity]": 1,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "metadata[tenant_id]": str(tenant_id),
                "metadata[plan]": plan.value,
                "subscription_data[metadata][tenant_id]": str(tenant_id),
                "subscription_data[metadata][plan]": plan.value,
            },
        )
        return CheckoutSession(url=str(data["url"]), session_id=str(data["id"]), provider=self.name)

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        verify_stripe_signature(header(headers, STRIPE_SIGNATURE_HEADER), body, self.webhook_secret)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise BillingError("stripe webhook body is not JSON") from exc
        if not isinstance(payload, dict):
            raise BillingError("stripe webhook body is not an object")
        return payload

    def parse_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> BillingEvent | None:
        return parse_stripe_event(payload, price_ids=self.price_ids)
