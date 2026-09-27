"""BillingProvider protocol and the provider-neutral BillingService (SPEC 3, 10.1, 10.3).

    providers = providers_from_settings(settings)          # {"stripe": ..., "razorpay": ...}
    provider = providers[provider_for_region(tenant.region)]
    url = await provider.create_checkout(customer_id, plan=Plan.PRO, tenant=..., ...)

    event = provider.parse_event(provider.verify_webhook(headers, body))
    outcome = await BillingService(database).apply_event(event)   # idempotent by event_id

apply_event() records the event in billing_events (unique per provider + event id, so
provider retries are no-ops), upserts billing_customers, and on checkout/subscription
events moves tenants.plan and writes a usage_ledger row (metric plan_change). PlanService
reads tenants.plan on every check, so the new limits apply to the next request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing import (
    PLAN_CHANGE_METRIC,
    BillingEvent,
    BillingEventKind,
    BillingProviderName,
    changes_plan,
)
from app.core.db import Database, get_database
from app.core.plan import Plan, period_key
from app.models import BillingCustomer, BillingEventRecord, Tenant, UsageLedger
from app.services.audit import audit

log = structlog.get_logger(__name__)

# audit_log action for a verified provider webhook (no user: the caller is the provider).
BILLING_WEBHOOK_ACTION = "billing.webhook"

# Events whose `status` describes the subscription (and not an invoice or payment).
SUBSCRIPTION_KINDS = frozenset(
    {
        BillingEventKind.CHECKOUT_COMPLETED,
        BillingEventKind.SUBSCRIPTION_UPDATED,
        BillingEventKind.SUBSCRIPTION_CANCELLED,
    }
)
# Subscription states that still entitle the tenant to its paid plan.
ACTIVE_STATUSES = frozenset({"active", "trialing", "authenticated", "created", "complete"})


class BillingError(Exception):
    """Provider API failure (non-2xx, network) surfaced to the API as 502."""


@dataclass(frozen=True, slots=True)
class CustomerRef:
    id: str
    gst_details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    url: str
    session_id: str
    provider: BillingProviderName


class BillingProvider(Protocol):
    name: BillingProviderName

    @property
    def configured(self) -> bool: ...

    async def create_customer(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        email: str,
        gst_details: dict[str, Any] | None = None,
    ) -> CustomerRef: ...

    async def create_checkout(
        self,
        *,
        customer_id: str,
        tenant_id: uuid.UUID,
        plan: Plan,
        success_url: str,
        cancel_url: str,
        gst_details: dict[str, Any] | None = None,
    ) -> CheckoutSession: ...

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> dict[str, Any]: ...

    def parse_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> BillingEvent | None: ...


def header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup over a plain dict."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


@dataclass(slots=True)
class ApplyOutcome:
    status: str  # processed | duplicate | ignored
    tenant_id: uuid.UUID | None = None
    plan_before: Plan | None = None
    plan_after: Plan | None = None
    event_id: str | None = None
    kind: BillingEventKind | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "tenant_id": None if self.tenant_id is None else str(self.tenant_id),
            "plan_before": None if self.plan_before is None else self.plan_before.value,
            "plan_after": None if self.plan_after is None else self.plan_after.value,
            "event_id": self.event_id,
            "kind": None if self.kind is None else self.kind.value,
        }


class BillingService:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or get_database()

    # -- tenant resolution ----------------------------------------------------------------
    async def resolve_tenant(self, event: BillingEvent) -> uuid.UUID | None:
        """Tenant from the event metadata, else from billing_customers by provider ids.

        The lookup runs on the owner role: a webhook has no tenant context yet and the
        payload's signature has already been verified.
        """
        if event.tenant_id:
            try:
                return uuid.UUID(event.tenant_id)
            except ValueError:
                log.warning("billing.bad_tenant_id", tenant_id=event.tenant_id)
        customer = event.external_ids.get("customer")
        subscription = event.external_ids.get("subscription")
        if not customer and not subscription:
            return None
        async with self.database.owner_session() as session:
            stmt = select(BillingCustomer.tenant_id).where(
                BillingCustomer.provider == event.provider.value
            )
            if subscription:
                row = (
                    await session.execute(
                        stmt.where(BillingCustomer.subscription_id == subscription)
                    )
                ).first()
                if row is not None:
                    return uuid.UUID(str(row[0]))
            if customer:
                row = (
                    await session.execute(stmt.where(BillingCustomer.customer_id == customer))
                ).first()
                if row is not None:
                    return uuid.UUID(str(row[0]))
        return None

    # -- event application ----------------------------------------------------------------
    async def apply_event(
        self, event: BillingEvent, *, payload: dict[str, Any] | None = None
    ) -> ApplyOutcome:
        tenant_id = await self.resolve_tenant(event)
        if tenant_id is None:
            log.warning("billing.event_without_tenant", provider=event.provider, id=event.event_id)
            return ApplyOutcome("ignored", event_id=event.event_id, kind=event.kind)
        async with self.database.session(tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                log.warning("billing.unknown_tenant", tenant_id=str(tenant_id))
                return ApplyOutcome("ignored", event_id=event.event_id, kind=event.kind)
            duplicate = (
                await session.execute(
                    select(BillingEventRecord.id).where(
                        BillingEventRecord.provider == event.provider.value,
                        BillingEventRecord.event_id == event.event_id,
                    )
                )
            ).first()
            if duplicate is not None:
                return ApplyOutcome(
                    "duplicate",
                    tenant_id=tenant_id,
                    plan_before=Plan(tenant.plan),
                    plan_after=Plan(tenant.plan),
                    event_id=event.event_id,
                    kind=event.kind,
                )
            before = Plan(tenant.plan)
            await self._upsert_customer(session, tenant, event)
            after = before
            if changes_plan(event) and event.plan is not None and event.plan is not before:
                tenant.plan = event.plan
                after = event.plan
                session.add(
                    UsageLedger(
                        tenant_id=tenant.id,
                        metric=PLAN_CHANGE_METRIC,
                        quantity=1,
                        period=period_key(PLAN_CHANGE_METRIC),
                        ref=f"{event.provider.value}:{event.event_id}:{before.value}->{after.value}"[
                            :200
                        ],
                    )
                )
            session.add(
                BillingEventRecord(
                    tenant_id=tenant.id,
                    provider=event.provider.value,
                    event_id=event.event_id,
                    kind=event.kind.value,
                    plan=event.plan,
                    amount=event.amount,
                    currency=event.currency,
                    payload=payload or {},
                    gst=event.gst,
                )
            )
            # A webhook has no user, so the audit middleware records nothing for it: the
            # effect is audited here instead, in the tenant the event landed in.
            await audit(
                session,
                BILLING_WEBHOOK_ACTION,
                ("tenant", str(tenant.id)),
                tenant_id=tenant.id,
                meta={
                    "provider": event.provider.value,
                    "kind": event.kind.value,
                    "event_id": event.event_id,
                    "plan_before": before.value,
                    "plan_after": after.value,
                },
            )
            await session.flush()
            log.info(
                "billing.event_processed",
                provider=event.provider.value,
                kind=event.kind.value,
                tenant_id=str(tenant.id),
                plan_before=before.value,
                plan_after=after.value,
            )
            return ApplyOutcome(
                "processed",
                tenant_id=tenant.id,
                plan_before=before,
                plan_after=after,
                event_id=event.event_id,
                kind=event.kind,
            )

    async def _upsert_customer(
        self, session: AsyncSession, tenant: Tenant, event: BillingEvent
    ) -> BillingCustomer | None:
        customer_id = event.external_ids.get("customer")
        row = (
            await session.execute(
                select(BillingCustomer).where(
                    BillingCustomer.tenant_id == tenant.id,
                    BillingCustomer.provider == event.provider.value,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            if not customer_id:
                return None
            row = BillingCustomer(
                tenant_id=tenant.id, provider=event.provider.value, customer_id=customer_id
            )
            session.add(row)
        elif customer_id and row.customer_id != customer_id:
            row.customer_id = customer_id
        subscription = event.external_ids.get("subscription")
        if subscription:
            row.subscription_id = subscription
        # billing_customers.status tracks the SUBSCRIPTION, so only checkout/subscription
        # events set it: an invoice event's status is the invoice's ('paid', 'failed').
        if event.status and event.kind in SUBSCRIPTION_KINDS:
            row.status = event.status
        if event.kind is BillingEventKind.SUBSCRIPTION_CANCELLED:
            row.status = "canceled"
        elif event.kind is BillingEventKind.INVOICE_FAILED and row.status not in ("canceled",):
            row.status = "past_due"
        if changes_plan(event) and event.plan is not None:
            row.plan = event.plan
        if event.current_period_end:
            row.current_period_end = datetime.fromtimestamp(event.current_period_end, tz=UTC)
        if event.gst:
            merged = dict(row.gst_details or {})
            for key in ("recipient_gstin", "place_of_supply"):
                if event.gst.get(key):
                    merged[key.replace("recipient_", "")] = event.gst[key]
            row.gst_details = merged
        await session.flush()
        return row
