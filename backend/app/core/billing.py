"""Billing logic that needs no I/O (SPEC 3 plans, 10.1 billing, 10.3 webhooks).

Provider choice follows the tenant's region: US tenants pay in USD through Stripe, IN
tenants pay in INR through Razorpay with GST invoices. This module holds the webhook
signature checks, the provider-neutral BillingEvent and the parsers that turn raw
Stripe / Razorpay webhook payloads into it, plus the GST arithmetic for Indian invoices.
services.billing does the HTTP and database work.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.config import Region
from app.core.plan import Plan

STRIPE_SIGNATURE_HEADER = "Stripe-Signature"
RAZORPAY_SIGNATURE_HEADER = "X-Razorpay-Signature"
STRIPE_SIGNATURE_TOLERANCE_SECONDS = 300
# SAC 998314 = "Information technology (IT) design and development services" (GST tariff).
DEFAULT_HSN_SAC = "998314"
DEFAULT_GST_RATE_PCT = 18
PLAN_CHANGE_METRIC = "plan_change"
PAID_PLANS = (Plan.PRO, Plan.ENTERPRISE)


class BillingProviderName(StrEnum):
    STRIPE = "stripe"
    RAZORPAY = "razorpay"


class BillingEventKind(StrEnum):
    CHECKOUT_COMPLETED = "checkout_completed"
    SUBSCRIPTION_UPDATED = "subscription_updated"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    INVOICE_PAID = "invoice_paid"
    INVOICE_FAILED = "invoice_failed"


class SignatureError(ValueError):
    """Raised when a webhook signature is missing, malformed, stale or wrong."""


def provider_for_region(region: Region | str) -> BillingProviderName:
    """us -> Stripe (USD), in -> Razorpay (INR)."""
    return (
        BillingProviderName.RAZORPAY if Region(region) is Region.IN else BillingProviderName.STRIPE
    )


def currency_for_provider(provider: BillingProviderName | str) -> str:
    return "INR" if BillingProviderName(provider) is BillingProviderName.RAZORPAY else "USD"


@dataclass(frozen=True, slots=True)
class BillingEvent:
    """Provider-neutral view of one webhook event.

    amount is in the currency's minor unit (cents / paise). plan is None when the event
    does not change the plan (invoice events, or a subscription without a known price).
    external_ids carries the provider's customer / subscription / invoice ids.
    """

    provider: BillingProviderName
    event_id: str
    kind: BillingEventKind
    tenant_id: str | None
    plan: Plan | None = None
    status: str | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    amount: int | None = None
    currency: str | None = None
    current_period_end: int | None = None  # unix seconds
    gst: dict[str, Any] = field(default_factory=dict)
    raw_type: str = ""


# --- signatures ---------------------------------------------------------------------------


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def stripe_signature_header(body: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header value (used by tests and the local CLI)."""
    ts = int(time.time()) if timestamp is None else int(timestamp)
    return f"t={ts},v1={_hmac_hex(secret, f'{ts}.'.encode() + body)}"


def verify_stripe_signature(
    header: str | None,
    body: bytes,
    secret: str,
    *,
    tolerance_seconds: int = STRIPE_SIGNATURE_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Stripe scheme: HMAC-SHA256 over '<t>.<payload>' with the endpoint secret; any `v1`
    entry may match; `t` must be within the tolerance window (replay protection)."""
    if not secret:
        raise SignatureError("webhook secret not configured")
    if not header:
        raise SignatureError("missing Stripe-Signature header")
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        raise SignatureError("malformed Stripe-Signature header")
    moment = time.time() if now is None else now
    if abs(moment - timestamp) > tolerance_seconds:
        raise SignatureError("Stripe-Signature timestamp outside tolerance")
    expected = _hmac_hex(secret, f"{timestamp}.".encode() + body)
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise SignatureError("Stripe-Signature mismatch")


def razorpay_signature(body: bytes, secret: str) -> str:
    return _hmac_hex(secret, body)


def verify_razorpay_signature(header: str | None, body: bytes, secret: str) -> None:
    """Razorpay scheme: X-Razorpay-Signature = HMAC-SHA256(body, webhook secret), hex."""
    if not secret:
        raise SignatureError("webhook secret not configured")
    if not header:
        raise SignatureError("missing X-Razorpay-Signature header")
    if not hmac.compare_digest(razorpay_signature(body, secret), header.strip()):
        raise SignatureError("X-Razorpay-Signature mismatch")


# --- GST (India) --------------------------------------------------------------------------


def gstin_state_code(gstin: str | None) -> str | None:
    """The first two digits of a GSTIN are the state code (place of business)."""
    if not gstin or len(gstin) < 2 or not gstin[:2].isdigit():
        return None
    return gstin[:2]


def gst_breakdown(
    total_paise: int,
    *,
    supplier_gstin: str,
    place_of_supply: str | None,
    recipient_gstin: str | None = None,
    rate_pct: int = DEFAULT_GST_RATE_PCT,
    hsn_sac: str = DEFAULT_HSN_SAC,
    invoice_number: str | None = None,
) -> dict[str, Any]:
    """Split a GST-inclusive INR amount (paise) into taxable value and tax.

    Intra-state supply (place of supply == supplier's state) splits the tax into CGST +
    SGST; inter-state supply charges IGST. The place of supply defaults to the recipient's
    GSTIN state. Rounding: taxable value is rounded to the paise, tax = total - taxable so
    the lines always add up to the amount actually charged.
    """
    if total_paise < 0:
        raise ValueError("total_paise must be >= 0")
    if not 0 <= rate_pct <= 100:
        raise ValueError("rate_pct must be between 0 and 100")
    supplier_state = gstin_state_code(supplier_gstin)
    pos = place_of_supply or gstin_state_code(recipient_gstin)
    taxable = round(total_paise * 100 / (100 + rate_pct))
    tax = total_paise - taxable
    intra_state = bool(pos and supplier_state and pos == supplier_state)
    out: dict[str, Any] = {
        "supplier_gstin": supplier_gstin,
        "recipient_gstin": recipient_gstin,
        "place_of_supply": pos,
        "hsn_sac": hsn_sac,
        "tax_rate_pct": rate_pct,
        "currency": "INR",
        "taxable_paise": taxable,
        "total_paise": total_paise,
        "invoice_number": invoice_number,
        "supply_type": "intra_state" if intra_state else "inter_state",
    }
    if intra_state:
        cgst = tax // 2
        out.update({"cgst_paise": cgst, "sgst_paise": tax - cgst, "igst_paise": 0})
    else:
        out.update({"cgst_paise": 0, "sgst_paise": 0, "igst_paise": tax})
    return out


# --- payload parsing ----------------------------------------------------------------------


def _plan_from(value: Any) -> Plan | None:
    if isinstance(value, str):
        try:
            return Plan(value.lower())
        except ValueError:
            return None
    return None


def plan_for_price(price_ids: dict[str, str], price_id: str | None) -> Plan | None:
    """Reverse lookup of STRIPE_PRICE_IDS / RAZORPAY_PLAN_IDS ({plan: provider id})."""
    if not price_id:
        return None
    for plan, configured in price_ids.items():
        if configured == price_id:
            return _plan_from(plan)
    return None


def _get(data: Any, *path: str) -> Any:
    """Nested lookup over dicts (and lists by numeric key); None when any hop is missing."""
    for key in path:
        if isinstance(data, list):
            index = int(key) if key.isdigit() else -1
            data = data[index] if 0 <= index < len(data) else None
        elif isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):  # expanded Stripe objects
        value = value.get("id")
    return None if value is None else str(value)


def _stripe_tenant_id(obj: dict[str, Any]) -> str | None:
    for path in (
        ("metadata", "tenant_id"),
        ("client_reference_id",),
        ("subscription_details", "metadata", "tenant_id"),
        ("parent", "subscription_details", "metadata", "tenant_id"),
    ):
        value = _get(obj, *path)
        if value:
            return str(value)
    lines = _get(obj, "lines", "data")
    if isinstance(lines, list) and lines:
        value = _get(lines[0], "metadata", "tenant_id")
        if value:
            return str(value)
    return None


def _stripe_plan(obj: dict[str, Any], price_ids: dict[str, str]) -> Plan | None:
    plan = _plan_from(_get(obj, "metadata", "plan"))
    if plan is not None:
        return plan
    items = _get(obj, "items", "data")
    if isinstance(items, list) and items:
        return plan_for_price(price_ids, _str(_get(items[0], "price", "id")))
    return None


STRIPE_KINDS: dict[str, BillingEventKind] = {
    "checkout.session.completed": BillingEventKind.CHECKOUT_COMPLETED,
    "customer.subscription.created": BillingEventKind.SUBSCRIPTION_UPDATED,
    "customer.subscription.updated": BillingEventKind.SUBSCRIPTION_UPDATED,
    "customer.subscription.deleted": BillingEventKind.SUBSCRIPTION_CANCELLED,
    "invoice.paid": BillingEventKind.INVOICE_PAID,
    "invoice.payment_succeeded": BillingEventKind.INVOICE_PAID,
    "invoice.payment_failed": BillingEventKind.INVOICE_FAILED,
}


def parse_stripe_event(
    payload: dict[str, Any], *, price_ids: dict[str, str] | None = None
) -> BillingEvent | None:
    """Stripe event envelope -> BillingEvent; None for event types we do not handle."""
    price_ids = price_ids or {}
    event_type = str(payload.get("type") or "")
    kind = STRIPE_KINDS.get(event_type)
    event_id = _str(payload.get("id"))
    obj = _get(payload, "data", "object")
    if kind is None or not event_id or not isinstance(obj, dict):
        return None
    status = _str(obj.get("status"))
    plan: Plan | None = None
    period_end = obj.get("current_period_end")
    external: dict[str, str] = {}
    for name, key in (("customer", "customer"), ("subscription", "subscription")):
        value = _str(obj.get(key))
        if value:
            external[name] = value
    amount: int | None = None
    if kind is BillingEventKind.CHECKOUT_COMPLETED:
        external["checkout_session"] = str(obj.get("id"))
        plan = _plan_from(_get(obj, "metadata", "plan"))
        amount = obj.get("amount_total")
    elif kind in (BillingEventKind.SUBSCRIPTION_UPDATED, BillingEventKind.SUBSCRIPTION_CANCELLED):
        external["subscription"] = str(obj.get("id"))
        plan = _stripe_plan(obj, price_ids)
        if kind is BillingEventKind.SUBSCRIPTION_UPDATED and status in ("canceled", "unpaid"):
            kind = BillingEventKind.SUBSCRIPTION_CANCELLED
    else:
        external["invoice"] = str(obj.get("id"))
        if obj.get("number"):
            external["invoice_number"] = str(obj["number"])
        if obj.get("hosted_invoice_url"):
            external["invoice_url"] = str(obj["hosted_invoice_url"])
        amount = obj.get("amount_paid") if kind is BillingEventKind.INVOICE_PAID else None
        if amount is None:
            amount = obj.get("amount_due")
        period_end = _get(obj, "lines", "data", "0", "period", "end") or period_end
    if kind is BillingEventKind.SUBSCRIPTION_CANCELLED:
        plan = Plan.FREE
    return BillingEvent(
        provider=BillingProviderName.STRIPE,
        event_id=event_id,
        kind=kind,
        tenant_id=_stripe_tenant_id(obj),
        plan=plan,
        status=status,
        external_ids=external,
        amount=int(amount) if isinstance(amount, int) else None,
        currency=str(obj["currency"]).upper() if obj.get("currency") else "USD",
        current_period_end=int(period_end) if isinstance(period_end, int) else None,
        raw_type=event_type,
    )


RAZORPAY_KINDS: dict[str, BillingEventKind] = {
    "subscription.authenticated": BillingEventKind.CHECKOUT_COMPLETED,
    "subscription.activated": BillingEventKind.SUBSCRIPTION_UPDATED,
    "subscription.charged": BillingEventKind.SUBSCRIPTION_UPDATED,
    "subscription.updated": BillingEventKind.SUBSCRIPTION_UPDATED,
    "subscription.resumed": BillingEventKind.SUBSCRIPTION_UPDATED,
    "subscription.pending": BillingEventKind.INVOICE_FAILED,
    "subscription.halted": BillingEventKind.INVOICE_FAILED,
    "subscription.cancelled": BillingEventKind.SUBSCRIPTION_CANCELLED,
    "subscription.completed": BillingEventKind.SUBSCRIPTION_CANCELLED,
    "invoice.paid": BillingEventKind.INVOICE_PAID,
    "payment.failed": BillingEventKind.INVOICE_FAILED,
}


def parse_razorpay_event(
    payload: dict[str, Any],
    *,
    plan_ids: dict[str, str] | None = None,
    event_id: str | None = None,
    supplier_gstin: str = "",
    gst_rate_pct: int = DEFAULT_GST_RATE_PCT,
) -> BillingEvent | None:
    """Razorpay webhook envelope ({event, payload: {subscription|invoice|payment: {entity}}}).

    Razorpay identifies deliveries by the X-Razorpay-Event-Id header, passed as `event_id`;
    without it the entity id + event name is used so retries still dedupe.
    """
    plan_ids = plan_ids or {}
    event_type = str(payload.get("event") or "")
    kind = RAZORPAY_KINDS.get(event_type)
    if kind is None:
        return None
    sub = _get(payload, "payload", "subscription", "entity")
    invoice = _get(payload, "payload", "invoice", "entity")
    payment = _get(payload, "payload", "payment", "entity")
    sub = sub if isinstance(sub, dict) else {}
    invoice = invoice if isinstance(invoice, dict) else {}
    payment = payment if isinstance(payment, dict) else {}
    primary = sub or invoice or payment
    if not primary:
        return None
    notes = {}
    for source in (sub, invoice, payment):
        candidate = source.get("notes")
        if isinstance(candidate, dict) and candidate:
            notes = candidate
            break
    tenant_id = _str(notes.get("tenant_id"))
    external: dict[str, str] = {}
    sub_id = _str(sub.get("id")) or _str(invoice.get("subscription_id"))
    if sub_id:
        external["subscription"] = sub_id
    customer_id = (
        _str(sub.get("customer_id"))
        or _str(invoice.get("customer_id"))
        or _str(payment.get("customer_id"))
    )
    if customer_id:
        external["customer"] = customer_id
    if invoice.get("id"):
        external["invoice"] = str(invoice["id"])
    if invoice.get("invoice_number"):
        external["invoice_number"] = str(invoice["invoice_number"])
    if invoice.get("short_url"):
        external["invoice_url"] = str(invoice["short_url"])
    if payment.get("id"):
        external["payment"] = str(payment["id"])
    plan = plan_for_price(plan_ids, _str(sub.get("plan_id"))) or _plan_from(notes.get("plan"))
    if kind is BillingEventKind.SUBSCRIPTION_CANCELLED:
        plan = Plan.FREE
    amount_raw = invoice.get("amount_paid") or invoice.get("amount") or payment.get("amount")
    amount = int(amount_raw) if isinstance(amount_raw, int) else None
    gst: dict[str, Any] = {}
    if kind is BillingEventKind.INVOICE_PAID and amount is not None and supplier_gstin:
        gst = gst_breakdown(
            amount,
            supplier_gstin=supplier_gstin,
            place_of_supply=_str(notes.get("place_of_supply")),
            recipient_gstin=_str(notes.get("gstin")),
            rate_pct=gst_rate_pct,
            invoice_number=external.get("invoice_number"),
        )
    period_end = sub.get("current_end")
    resolved_event_id = event_id or f"{event_type}:{primary.get('id')}"
    return BillingEvent(
        provider=BillingProviderName.RAZORPAY,
        event_id=resolved_event_id,
        kind=kind,
        tenant_id=tenant_id,
        plan=plan,
        status=_str(sub.get("status"))
        or _str(invoice.get("status"))
        or _str(payment.get("status")),
        external_ids=external,
        amount=amount,
        currency="INR",
        current_period_end=int(period_end) if isinstance(period_end, int) else None,
        gst=gst,
        raw_type=event_type,
    )


def changes_plan(event: BillingEvent) -> bool:
    """Only checkout / subscription events with a resolved plan move tenants.plan."""
    return event.plan is not None and event.kind in (
        BillingEventKind.CHECKOUT_COMPLETED,
        BillingEventKind.SUBSCRIPTION_UPDATED,
        BillingEventKind.SUBSCRIPTION_CANCELLED,
    )
