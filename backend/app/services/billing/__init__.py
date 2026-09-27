"""Billing providers behind one protocol (SPEC 10.1): Stripe (us, USD), Razorpay (in, INR)."""

from __future__ import annotations

from app.core.billing import BillingProviderName, provider_for_region
from app.core.config import Region, Settings, get_settings
from app.services.billing.base import (
    ACTIVE_STATUSES,
    ApplyOutcome,
    BillingError,
    BillingProvider,
    BillingService,
    CheckoutSession,
    CustomerRef,
)
from app.services.billing.razorpay import RazorpayProvider
from app.services.billing.stripe import StripeProvider

Providers = dict[BillingProviderName, BillingProvider]


def providers_from_settings(settings: Settings | None = None) -> Providers:
    settings = settings or get_settings()
    return {
        BillingProviderName.STRIPE: StripeProvider.from_settings(settings),
        BillingProviderName.RAZORPAY: RazorpayProvider.from_settings(settings),
    }


def provider_for_tenant_region(providers: Providers, region: Region | str) -> BillingProvider:
    return providers[provider_for_region(region)]


__all__ = [
    "ACTIVE_STATUSES",
    "ApplyOutcome",
    "BillingError",
    "BillingProvider",
    "BillingProviderName",
    "BillingService",
    "CheckoutSession",
    "CustomerRef",
    "Providers",
    "RazorpayProvider",
    "StripeProvider",
    "provider_for_tenant_region",
    "providers_from_settings",
]
