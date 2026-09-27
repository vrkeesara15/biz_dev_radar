"""In-memory BillingProvider double (M7-04). No HTTP, no signatures to configure.

Used where a test needs the billing routes to answer as if a provider were configured
without mocking its REST API (the isolation harness); tests that assert on the real
Stripe / Razorpay wire format use the real providers with respx instead.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.billing import (
    BillingEvent,
    BillingProviderName,
    SignatureError,
    currency_for_provider,
)
from app.core.plan import Plan
from app.services.billing.base import CheckoutSession, CustomerRef


class FakeBillingProvider:
    """Records the calls it receives and hands back deterministic ids."""

    def __init__(self, name: BillingProviderName, *, configured: bool = True) -> None:
        self.name = name
        self._configured = configured
        self.customers: list[dict[str, Any]] = []
        self.checkouts: list[dict[str, Any]] = []
        self.events: list[BillingEvent] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def create_customer(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        email: str,
        gst_details: dict[str, Any] | None = None,
    ) -> CustomerRef:
        self.customers.append({"tenant_id": tenant_id, "name": name, "email": email})
        return CustomerRef(
            id=f"{self.name.value}_cus_{len(self.customers)}", gst_details=dict(gst_details or {})
        )

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
        self.checkouts.append({"customer_id": customer_id, "tenant_id": tenant_id, "plan": plan})
        session_id = f"{self.name.value}_cs_{len(self.checkouts)}"
        return CheckoutSession(
            url=f"https://pay.example/{currency_for_provider(self.name).lower()}/{session_id}",
            session_id=session_id,
            provider=self.name,
        )

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        raise SignatureError("fake provider verifies no webhooks")

    def parse_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> BillingEvent | None:
        return None


def fake_providers() -> dict[BillingProviderName, Any]:
    return {name: FakeBillingProvider(name) for name in BillingProviderName}
