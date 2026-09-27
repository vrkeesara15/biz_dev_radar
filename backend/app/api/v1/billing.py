"""Tenant billing (SPEC 3, 10.1, 10.4 settings > billing).

POST /api/v1/billing/checkout {plan, success_url, cancel_url, gst?} (tenant_owner) starts a
hosted checkout with the provider chosen by the tenant's region (us -> Stripe, in ->
Razorpay). GET /api/v1/billing shows plan, subscription status, usage against plan_limits
and the next invoice date. Plan changes only ever arrive through the provider webhooks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import (
    TENANT_ROLES,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    require_role,
)
from app.core.billing import PAID_PLANS, BillingProviderName, provider_for_region
from app.core.config import Region
from app.core.plan import Plan, Resource
from app.core.profile_fields import normalize_gstin
from app.core.roles import Role
from app.models import BillingCustomer, BillingEventRecord, Tenant
from app.services.audit import AuditHint
from app.services.billing import ACTIVE_STATUSES, BillingError, Providers
from app.services.plan import PlanService
from app.services.profiles import PROFILE_COUNTERS

router = APIRouter(prefix="/billing", tags=["billing"])

OwnerDep = Annotated[CurrentUser, Depends(require_role(Role.TENANT_OWNER))]
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]


def get_billing_providers(request: Request) -> Providers:
    providers: Providers = request.app.state.billing_providers
    return providers


ProvidersDep = Annotated[Providers, Depends(get_billing_providers)]


class GstDetails(BaseModel):
    gstin: str | None = Field(default=None, max_length=15)
    # ISO 3166-2:IN numeric state code as printed on GST invoices ("29" = Karnataka)
    place_of_supply: str | None = Field(default=None, min_length=2, max_length=2)
    legal_name: str | None = Field(default=None, max_length=200)

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, value: str | None) -> str | None:
        return None if value is None else normalize_gstin(value)

    @field_validator("place_of_supply")
    @classmethod
    def _pos(cls, value: str | None) -> str | None:
        if value is not None and not value.isdigit():
            raise ValueError("place_of_supply must be the two-digit GST state code")
        return value


class CheckoutIn(BaseModel):
    plan: Plan
    success_url: str = Field(max_length=2000)
    cancel_url: str = Field(max_length=2000)
    gst: GstDetails | None = None

    @field_validator("plan")
    @classmethod
    def _paid(cls, value: Plan) -> Plan:
        if value not in PAID_PLANS:
            raise ValueError("checkout is only for paid plans (pro, enterprise)")
        return value

    @field_validator("success_url", "cancel_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith(("https://", "http://localhost")):
            raise ValueError("return URLs must be https")
        return value


class CheckoutOut(BaseModel):
    provider: BillingProviderName
    url: str
    session_id: str
    plan: Plan
    currency: str


class LimitUsage(BaseModel):
    resource: str
    limit: int | None
    used: int
    remaining: int | None


class InvoiceOut(BaseModel):
    amount: int | None
    currency: str | None
    paid_at: datetime
    number: str | None
    url: str | None
    gst: dict[str, Any]


class BillingOut(BaseModel):
    plan: Plan
    provider: BillingProviderName
    currency: str
    status: str
    subscription_id: str | None
    current_period_end: datetime | None
    next_invoice_at: datetime | None
    gst_details: dict[str, Any]
    limits: list[LimitUsage]
    last_invoice: InvoiceOut | None


async def _tenant(session: TenantSessionDep, user: CurrentUser) -> Tenant:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    return tenant


@router.post("/checkout", response_model=CheckoutOut, status_code=status.HTTP_201_CREATED)
async def create_checkout(
    body: CheckoutIn,
    user: OwnerDep,
    session: TenantSessionDep,
    providers: ProvidersDep,
    request: Request,
) -> CheckoutOut:
    """Start a hosted checkout for a paid plan with the region's provider."""
    tenant = await _tenant(session, user)
    provider_name = provider_for_region(tenant.region)
    provider = providers[provider_name]
    if not provider.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "billing_not_configured", "provider": provider_name.value},
        )
    gst = body.gst.model_dump(exclude_none=True) if body.gst else {}
    if gst and tenant.region is not Region.IN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "region_mismatch", "fields": ["gst"]},
        )
    customer = (
        await session.execute(
            select(BillingCustomer).where(
                BillingCustomer.tenant_id == tenant.id,
                BillingCustomer.provider == provider_name.value,
            )
        )
    ).scalar_one_or_none()
    try:
        if customer is None:
            ref = await provider.create_customer(
                tenant_id=tenant.id, name=tenant.name, email=user.email, gst_details=gst
            )
            customer = BillingCustomer(
                tenant_id=tenant.id,
                provider=provider_name.value,
                customer_id=ref.id,
                gst_details={**ref.gst_details, **gst},
            )
            session.add(customer)
            await session.flush()
        elif gst:
            customer.gst_details = {**(customer.gst_details or {}), **gst}
        checkout = await provider.create_checkout(
            customer_id=customer.customer_id,
            tenant_id=tenant.id,
            plan=body.plan,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            gst_details=customer.gst_details or None,
        )
    except BillingError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail={"error": "billing_provider_error"}
        ) from exc
    request.state.audit = AuditHint(
        action="billing.checkout",
        object_type="tenant",
        object_id=str(tenant.id),
        meta={"plan": body.plan.value, "provider": provider_name.value},
    )
    return CheckoutOut(
        provider=provider_name,
        url=checkout.url,
        session_id=checkout.session_id,
        plan=body.plan,
        currency="INR" if provider_name is BillingProviderName.RAZORPAY else "USD",
    )


@router.get("", response_model=BillingOut)
async def read_billing(
    user: ReaderDep, session: TenantSessionDep, settings: SettingsDep
) -> BillingOut:
    """Plan, subscription status, usage against plan_limits and the last / next invoice."""
    tenant = await _tenant(session, user)
    provider_name = provider_for_region(tenant.region)
    customer = (
        await session.execute(
            select(BillingCustomer).where(
                BillingCustomer.tenant_id == tenant.id,
                BillingCustomer.provider == provider_name.value,
            )
        )
    ).scalar_one_or_none()
    plan_service = PlanService(session, counters=PROFILE_COUNTERS)
    limits: list[LimitUsage] = []
    for resource in Resource:
        limit = await plan_service.limit_for(tenant, resource)
        used = await plan_service.usage(tenant.id, resource)
        limits.append(
            LimitUsage(
                resource=resource.value,
                limit=limit,
                used=used,
                remaining=None if limit is None else max(limit - used, 0),
            )
        )
    last_paid = (
        await session.execute(
            select(BillingEventRecord)
            .where(
                BillingEventRecord.tenant_id == tenant.id,
                BillingEventRecord.kind == "invoice_paid",
            )
            .order_by(BillingEventRecord.processed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    last_invoice = None
    if last_paid is not None:
        ids = last_paid.payload.get("_external_ids", {}) if last_paid.payload else {}
        last_invoice = InvoiceOut(
            amount=last_paid.amount,
            currency=last_paid.currency,
            paid_at=last_paid.processed_at,
            number=ids.get("invoice_number"),
            url=ids.get("invoice_url"),
            gst=last_paid.gst or {},
        )
    period_end = customer.current_period_end if customer else None
    active = customer is not None and customer.status in ACTIVE_STATUSES
    return BillingOut(
        plan=Plan(tenant.plan),
        provider=provider_name,
        currency="INR" if provider_name is BillingProviderName.RAZORPAY else "USD",
        status=customer.status if customer else "none",
        subscription_id=customer.subscription_id if customer else None,
        current_period_end=period_end,
        next_invoice_at=period_end if active else None,
        gst_details=(customer.gst_details if customer else None) or {},
        limits=limits,
        last_invoice=last_invoice,
    )
