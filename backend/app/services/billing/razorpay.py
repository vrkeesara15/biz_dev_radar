"""Razorpay provider (IN tenants, INR, GST invoices). Raw REST via httpx (Basic auth
key_id:key_secret); tests mock with respx.

Checkout = a Razorpay Subscription on the plan's plan id; its `short_url` is the hosted
payment page. The tenant id, plan and the customer's GST details (GSTIN, place of supply,
legal name) travel in `notes`, which Razorpay echoes on every subscription / invoice /
payment webhook, so invoices can be attributed and the GST split recorded (core.billing).
Amounts are paise.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from app.core.billing import (
    RAZORPAY_SIGNATURE_HEADER,
    BillingEvent,
    BillingProviderName,
    parse_razorpay_event,
    verify_razorpay_signature,
)
from app.core.config import Settings
from app.core.plan import Plan
from app.services.billing.base import BillingError, CheckoutSession, CustomerRef, header

RAZORPAY_EVENT_ID_HEADER = "X-Razorpay-Event-Id"
# 12 monthly charges before the subscription needs renewing (Razorpay requires total_count)
DEFAULT_TOTAL_COUNT = 12


class RazorpayProvider:
    name = BillingProviderName.RAZORPAY

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        plan_ids: dict[str, str],
        supplier_gstin: str = "",
        gst_rate_pct: int = 18,
        api_url: str = "https://api.razorpay.com/v1",
        timeout: float = 20.0,
    ) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret
        self.plan_ids = dict(plan_ids)
        self.supplier_gstin = supplier_gstin
        self.gst_rate_pct = gst_rate_pct
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> RazorpayProvider:
        return cls(
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
            plan_ids=settings.razorpay_plan_ids,
            supplier_gstin=settings.billing_gstin,
            gst_rate_pct=settings.billing_gst_rate_pct,
            api_url=settings.razorpay_api_url,
        )

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret and self.plan_ids)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.api_url}/{path.lstrip('/')}",
                    json=body,
                    auth=(self.key_id, self.key_secret),
                )
            except httpx.HTTPError as exc:
                raise BillingError(f"razorpay unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise BillingError(f"razorpay {path} -> {response.status_code}: {response.text[:300]}")
        data: dict[str, Any] = response.json()
        return data

    @staticmethod
    def _notes(
        tenant_id: uuid.UUID, plan: Plan | None, gst: dict[str, Any] | None
    ) -> dict[str, str]:
        notes = {"tenant_id": str(tenant_id)}
        if plan is not None:
            notes["plan"] = plan.value
        for key in ("gstin", "place_of_supply", "legal_name"):
            value = (gst or {}).get(key)
            if value:
                notes[key] = str(value)
        return notes

    async def create_customer(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        email: str,
        gst_details: dict[str, Any] | None = None,
    ) -> CustomerRef:
        gst = dict(gst_details or {})
        body: dict[str, Any] = {
            "name": name,
            "email": email,
            "fail_existing": "0",
            "notes": self._notes(tenant_id, None, gst),
        }
        if gst.get("gstin"):
            body["gstin"] = gst["gstin"]
        data = await self._post("customers", body)
        return CustomerRef(id=str(data["id"]), gst_details=gst)

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
        plan_id = self.plan_ids.get(plan.value)
        if not plan_id:
            raise BillingError(f"no Razorpay plan configured for plan {plan.value}")
        data = await self._post(
            "subscriptions",
            {
                "plan_id": plan_id,
                "customer_id": customer_id,
                "total_count": DEFAULT_TOTAL_COUNT,
                "quantity": 1,
                "customer_notify": 1,
                "notes": self._notes(tenant_id, plan, gst_details),
            },
        )
        return CheckoutSession(
            url=str(data["short_url"]), session_id=str(data["id"]), provider=self.name
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        verify_razorpay_signature(
            header(headers, RAZORPAY_SIGNATURE_HEADER), body, self.webhook_secret
        )
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise BillingError("razorpay webhook body is not JSON") from exc
        if not isinstance(payload, dict):
            raise BillingError("razorpay webhook body is not an object")
        return payload

    def parse_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> BillingEvent | None:
        return parse_razorpay_event(
            payload,
            plan_ids=self.plan_ids,
            event_id=header(headers or {}, RAZORPAY_EVENT_ID_HEADER),
            supplier_gstin=self.supplier_gstin,
            gst_rate_pct=self.gst_rate_pct,
        )
