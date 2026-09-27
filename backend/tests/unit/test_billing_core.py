"""M7-04: pure billing logic — provider choice, webhook signatures, GST, event parsing."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from app.core.billing import (
    DEFAULT_HSN_SAC,
    BillingEventKind,
    BillingProviderName,
    SignatureError,
    changes_plan,
    currency_for_provider,
    gst_breakdown,
    gstin_state_code,
    parse_razorpay_event,
    parse_stripe_event,
    plan_for_price,
    provider_for_region,
    razorpay_signature,
    stripe_signature_header,
    verify_razorpay_signature,
    verify_stripe_signature,
)
from app.core.config import Region
from app.core.plan import Plan

SECRET = "whsec_test_0123456789"


# --- provider choice ----------------------------------------------------------------------


def test_provider_follows_tenant_region() -> None:
    assert provider_for_region(Region.US) is BillingProviderName.STRIPE
    assert provider_for_region("us") is BillingProviderName.STRIPE
    assert provider_for_region(Region.IN) is BillingProviderName.RAZORPAY
    assert provider_for_region("in") is BillingProviderName.RAZORPAY


def test_currency_follows_provider() -> None:
    assert currency_for_provider(BillingProviderName.STRIPE) == "USD"
    assert currency_for_provider("razorpay") == "INR"


# --- Stripe signatures --------------------------------------------------------------------


def test_stripe_signature_roundtrip() -> None:
    body = b'{"id":"evt_1","type":"invoice.paid"}'
    header = stripe_signature_header(body, SECRET)
    verify_stripe_signature(header, body, SECRET)  # does not raise


def test_stripe_signature_accepts_several_v1_entries() -> None:
    body = b'{"id":"evt_1"}'
    ts = int(time.time())
    good = stripe_signature_header(body, SECRET, timestamp=ts).split("v1=")[1]
    verify_stripe_signature(f"t={ts},v1=deadbeef,v1={good}", body, SECRET)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "v1=abc",  # no timestamp
        "t=123",  # no signature
        "garbage",
    ],
)
def test_stripe_signature_rejects_missing_or_malformed_headers(header: str | None) -> None:
    with pytest.raises(SignatureError):
        verify_stripe_signature(header, b"{}", SECRET)


def test_stripe_signature_rejects_wrong_secret_and_tampered_body() -> None:
    body = b'{"id":"evt_1"}'
    header = stripe_signature_header(body, SECRET)
    with pytest.raises(SignatureError):
        verify_stripe_signature(header, body, "whsec_other")
    with pytest.raises(SignatureError):
        verify_stripe_signature(header, body + b" ", SECRET)


def test_stripe_signature_rejects_stale_timestamp() -> None:
    body = b'{"id":"evt_1"}'
    now = 1_700_000_000
    header = stripe_signature_header(body, SECRET, timestamp=now - 301)
    with pytest.raises(SignatureError, match="tolerance"):
        verify_stripe_signature(header, body, SECRET, now=now)
    # inside the 300 s window it is accepted
    fresh = stripe_signature_header(body, SECRET, timestamp=now - 299)
    verify_stripe_signature(fresh, body, SECRET, now=now)


def test_stripe_signature_requires_a_configured_secret() -> None:
    with pytest.raises(SignatureError, match="not configured"):
        verify_stripe_signature(stripe_signature_header(b"{}", SECRET), b"{}", "")


# --- Razorpay signatures ------------------------------------------------------------------


def test_razorpay_signature_roundtrip_and_rejections() -> None:
    body = b'{"event":"invoice.paid"}'
    verify_razorpay_signature(razorpay_signature(body, SECRET), body, SECRET)
    with pytest.raises(SignatureError):
        verify_razorpay_signature(None, body, SECRET)
    with pytest.raises(SignatureError):
        verify_razorpay_signature("0" * 64, body, SECRET)
    with pytest.raises(SignatureError):
        verify_razorpay_signature(razorpay_signature(body, SECRET), body + b"x", SECRET)
    with pytest.raises(SignatureError, match="not configured"):
        verify_razorpay_signature(razorpay_signature(body, SECRET), body, "")


# --- GST ------------------------------------------------------------------------------------

SUPPLIER = "29AAGCB7383J1ZL"  # 29 = Karnataka


def test_gstin_state_code() -> None:
    assert gstin_state_code(SUPPLIER) == "29"
    assert gstin_state_code(None) is None
    assert gstin_state_code("AB") is None


def test_gst_intra_state_splits_cgst_and_sgst() -> None:
    out = gst_breakdown(
        118_000, supplier_gstin=SUPPLIER, place_of_supply="29", invoice_number="INV-1"
    )
    assert out["supply_type"] == "intra_state"
    assert out["taxable_paise"] == 100_000
    assert out["cgst_paise"] == 9_000
    assert out["sgst_paise"] == 9_000
    assert out["igst_paise"] == 0
    assert out["tax_rate_pct"] == 18
    assert out["hsn_sac"] == DEFAULT_HSN_SAC
    assert out["currency"] == "INR"
    assert out["invoice_number"] == "INV-1"
    assert out["supplier_gstin"] == SUPPLIER
    total = out["taxable_paise"] + out["cgst_paise"] + out["sgst_paise"] + out["igst_paise"]
    assert total == out["total_paise"] == 118_000


def test_gst_inter_state_charges_igst_and_defaults_place_of_supply_to_the_recipient() -> None:
    out = gst_breakdown(
        118_000, supplier_gstin=SUPPLIER, place_of_supply=None, recipient_gstin="27AAACT2727Q1ZW"
    )
    assert out["place_of_supply"] == "27"
    assert out["supply_type"] == "inter_state"
    assert out["igst_paise"] == 18_000
    assert out["cgst_paise"] == out["sgst_paise"] == 0
    assert out["recipient_gstin"] == "27AAACT2727Q1ZW"


def test_gst_lines_always_add_up_to_the_charged_amount() -> None:
    for amount in (1, 7, 99, 100_001, 123_457):
        out = gst_breakdown(amount, supplier_gstin=SUPPLIER, place_of_supply="29")
        assert (
            out["taxable_paise"] + out["cgst_paise"] + out["sgst_paise"] + out["igst_paise"]
            == amount
        )


def test_gst_without_a_place_of_supply_is_inter_state() -> None:
    out = gst_breakdown(118_000, supplier_gstin=SUPPLIER, place_of_supply=None)
    assert out["supply_type"] == "inter_state"
    assert out["place_of_supply"] is None


def test_gst_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="total_paise"):
        gst_breakdown(-1, supplier_gstin=SUPPLIER, place_of_supply="29")
    with pytest.raises(ValueError, match="rate_pct"):
        gst_breakdown(100, supplier_gstin=SUPPLIER, place_of_supply="29", rate_pct=101)


# --- price/plan maps --------------------------------------------------------------------


def test_plan_for_price_reverse_lookup() -> None:
    prices = {"pro": "price_pro", "enterprise": "price_ent"}
    assert plan_for_price(prices, "price_pro") is Plan.PRO
    assert plan_for_price(prices, "price_ent") is Plan.ENTERPRISE
    assert plan_for_price(prices, "price_unknown") is None
    assert plan_for_price(prices, None) is None
    assert plan_for_price({"nonsense": "price_x"}, "price_x") is None


# --- Stripe payload parsing -----------------------------------------------------------------

TENANT = "11111111-1111-1111-1111-111111111111"
PRICES = {"pro": "price_pro", "enterprise": "price_ent"}


def stripe_event(event_type: str, obj: dict[str, Any], event_id: str = "evt_1") -> dict[str, Any]:
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


def test_parse_stripe_checkout_completed() -> None:
    event = parse_stripe_event(
        stripe_event(
            "checkout.session.completed",
            {
                "id": "cs_123",
                "customer": "cus_1",
                "subscription": "sub_1",
                "client_reference_id": TENANT,
                "metadata": {"tenant_id": TENANT, "plan": "pro"},
                "amount_total": 9900,
                "currency": "usd",
                "status": "complete",
            },
        ),
        price_ids=PRICES,
    )
    assert event is not None
    assert event.provider is BillingProviderName.STRIPE
    assert event.kind is BillingEventKind.CHECKOUT_COMPLETED
    assert event.event_id == "evt_1"
    assert event.tenant_id == TENANT
    assert event.plan is Plan.PRO
    assert event.amount == 9900
    assert event.currency == "USD"
    assert event.external_ids == {
        "customer": "cus_1",
        "subscription": "sub_1",
        "checkout_session": "cs_123",
    }
    assert changes_plan(event)


def test_parse_stripe_subscription_updated_resolves_plan_from_the_price_id() -> None:
    event = parse_stripe_event(
        stripe_event(
            "customer.subscription.updated",
            {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "active",
                "current_period_end": 1_800_000_000,
                "metadata": {"tenant_id": TENANT},
                "items": {"data": [{"price": {"id": "price_ent"}}]},
            },
            event_id="evt_2",
        ),
        price_ids=PRICES,
    )
    assert event is not None
    assert event.kind is BillingEventKind.SUBSCRIPTION_UPDATED
    assert event.plan is Plan.ENTERPRISE
    assert event.status == "active"
    assert event.current_period_end == 1_800_000_000
    assert event.external_ids["subscription"] == "sub_1"


def test_parse_stripe_subscription_updated_to_canceled_is_a_cancellation() -> None:
    event = parse_stripe_event(
        stripe_event(
            "customer.subscription.updated",
            {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "canceled",
                "metadata": {"tenant_id": TENANT},
                "items": {"data": [{"price": {"id": "price_pro"}}]},
            },
        ),
        price_ids=PRICES,
    )
    assert event is not None
    assert event.kind is BillingEventKind.SUBSCRIPTION_CANCELLED
    assert event.plan is Plan.FREE


def test_parse_stripe_subscription_deleted_drops_to_free() -> None:
    event = parse_stripe_event(
        stripe_event(
            "customer.subscription.deleted",
            {"id": "sub_1", "customer": "cus_1", "metadata": {"tenant_id": TENANT}},
        ),
        price_ids=PRICES,
    )
    assert event is not None
    assert event.kind is BillingEventKind.SUBSCRIPTION_CANCELLED
    assert event.plan is Plan.FREE
    assert changes_plan(event)


def test_parse_stripe_invoice_paid_carries_the_invoice_identifiers() -> None:
    event = parse_stripe_event(
        stripe_event(
            "invoice.paid",
            {
                "id": "in_1",
                "customer": "cus_1",
                "subscription": "sub_1",
                "number": "BR-0001",
                "hosted_invoice_url": "https://invoice.stripe.com/i/1",
                "amount_paid": 9900,
                "currency": "usd",
                "status": "paid",
                "lines": {"data": [{"period": {"end": 1_800_000_000}}]},
                "subscription_details": {"metadata": {"tenant_id": TENANT}},
            },
        ),
        price_ids=PRICES,
    )
    assert event is not None
    assert event.kind is BillingEventKind.INVOICE_PAID
    assert event.tenant_id == TENANT
    assert event.amount == 9900
    assert event.external_ids["invoice_number"] == "BR-0001"
    assert event.external_ids["invoice_url"] == "https://invoice.stripe.com/i/1"
    assert event.current_period_end == 1_800_000_000
    assert not changes_plan(event)  # invoices never move the plan


def test_parse_stripe_invoice_failed() -> None:
    event = parse_stripe_event(
        stripe_event(
            "invoice.payment_failed",
            {
                "id": "in_2",
                "customer": "cus_1",
                "amount_due": 9900,
                "currency": "usd",
                "metadata": {"tenant_id": TENANT},
            },
        )
    )
    assert event is not None
    assert event.kind is BillingEventKind.INVOICE_FAILED
    assert event.amount == 9900


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "evt", "type": "customer.created", "data": {"object": {"id": "cus_1"}}},
        {"type": "invoice.paid", "data": {"object": {"id": "in_1"}}},  # no event id
        {"id": "evt", "type": "invoice.paid"},  # no object
    ],
)
def test_parse_stripe_ignores_unhandled_or_malformed_events(payload: dict[str, Any]) -> None:
    assert parse_stripe_event(payload) is None


# --- Razorpay payload parsing ---------------------------------------------------------------

PLAN_IDS = {"pro": "plan_pro", "enterprise": "plan_ent"}


def razorpay_event(event: str, entities: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event,
        "payload": {name: {"entity": entity} for name, entity in entities.items()},
    }


def test_parse_razorpay_subscription_authenticated_is_a_checkout() -> None:
    event = parse_razorpay_event(
        razorpay_event(
            "subscription.authenticated",
            {
                "subscription": {
                    "id": "sub_rzp",
                    "plan_id": "plan_pro",
                    "customer_id": "cust_rzp",
                    "status": "authenticated",
                    "current_end": 1_800_000_000,
                    "notes": {"tenant_id": TENANT, "plan": "pro"},
                }
            },
        ),
        plan_ids=PLAN_IDS,
        event_id="rzp_evt_1",
    )
    assert event is not None
    assert event.provider is BillingProviderName.RAZORPAY
    assert event.kind is BillingEventKind.CHECKOUT_COMPLETED
    assert event.event_id == "rzp_evt_1"
    assert event.tenant_id == TENANT
    assert event.plan is Plan.PRO
    assert event.currency == "INR"
    assert event.external_ids == {"subscription": "sub_rzp", "customer": "cust_rzp"}


def test_parse_razorpay_falls_back_to_a_derived_event_id() -> None:
    event = parse_razorpay_event(
        razorpay_event(
            "subscription.activated",
            {"subscription": {"id": "sub_rzp", "plan_id": "plan_ent", "notes": {}}},
        ),
        plan_ids=PLAN_IDS,
    )
    assert event is not None
    assert event.event_id == "subscription.activated:sub_rzp"
    assert event.plan is Plan.ENTERPRISE
    assert event.tenant_id is None


def test_parse_razorpay_cancelled_drops_to_free() -> None:
    event = parse_razorpay_event(
        razorpay_event(
            "subscription.cancelled",
            {"subscription": {"id": "sub_rzp", "plan_id": "plan_pro", "notes": {}}},
        ),
        plan_ids=PLAN_IDS,
    )
    assert event is not None
    assert event.kind is BillingEventKind.SUBSCRIPTION_CANCELLED
    assert event.plan is Plan.FREE


def test_parse_razorpay_invoice_paid_computes_the_gst_split() -> None:
    event = parse_razorpay_event(
        razorpay_event(
            "invoice.paid",
            {
                "invoice": {
                    "id": "inv_rzp",
                    "subscription_id": "sub_rzp",
                    "customer_id": "cust_rzp",
                    "invoice_number": "BR-IN-0001",
                    "short_url": "https://rzp.io/i/abc",
                    "amount_paid": 118_000,
                    "status": "paid",
                    "notes": {
                        "tenant_id": TENANT,
                        "gstin": "29AABCU9603R1ZM",
                        "place_of_supply": "29",
                    },
                }
            },
        ),
        plan_ids=PLAN_IDS,
        event_id="rzp_evt_2",
        supplier_gstin=SUPPLIER,
    )
    assert event is not None
    assert event.kind is BillingEventKind.INVOICE_PAID
    assert event.amount == 118_000
    assert event.currency == "INR"
    assert event.external_ids["invoice_number"] == "BR-IN-0001"
    assert event.external_ids["invoice_url"] == "https://rzp.io/i/abc"
    assert event.gst["supply_type"] == "intra_state"
    assert event.gst["taxable_paise"] == 100_000
    assert event.gst["cgst_paise"] == event.gst["sgst_paise"] == 9_000
    assert event.gst["hsn_sac"] == DEFAULT_HSN_SAC
    assert event.gst["recipient_gstin"] == "29AABCU9603R1ZM"
    assert event.gst["invoice_number"] == "BR-IN-0001"


def test_parse_razorpay_invoice_without_a_supplier_gstin_records_no_gst() -> None:
    event = parse_razorpay_event(
        razorpay_event(
            "invoice.paid",
            {"invoice": {"id": "inv_rzp", "amount_paid": 118_000, "notes": {"tenant_id": TENANT}}},
        ),
        plan_ids=PLAN_IDS,
    )
    assert event is not None
    assert event.gst == {}


def test_parse_razorpay_payment_failed() -> None:
    event = parse_razorpay_event(
        razorpay_event(
            "payment.failed",
            {"payment": {"id": "pay_1", "amount": 118_000, "notes": {"tenant_id": TENANT}}},
        ),
        plan_ids=PLAN_IDS,
    )
    assert event is not None
    assert event.kind is BillingEventKind.INVOICE_FAILED
    assert event.external_ids["payment"] == "pay_1"


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "subscription.unknown", "payload": {}},
        {"event": "invoice.paid", "payload": {}},  # no entity
        json.loads("{}"),
    ],
)
def test_parse_razorpay_ignores_unhandled_or_empty_events(payload: dict[str, Any]) -> None:
    assert parse_razorpay_event(payload) is None
