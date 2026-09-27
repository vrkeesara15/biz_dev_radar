"""M5-09: the pricing plan never carries a price the rate card did not supply."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.pricing import (
    NEEDS_CATEGORY,
    NEEDS_PRICE,
    PricingPlan,
    RateCardRow,
    build_plan,
)

US_CARD = [
    RateCardRow("Solutions Architect", "hour", Decimal("210.50"), "USD", 10, "cleared"),
    RateCardRow("Cloud Engineer", "hour", Decimal("165.00"), "USD", 5),
]
IN_CARD = [
    RateCardRow("Project Manager", "month", Decimal("450000"), "INR", 12),
    RateCardRow("Cloud Engineer", "month", Decimal("280000"), "INR", 5),
]


def _placeholders(plan: PricingPlan) -> dict[str, str]:
    return {row.key: row.amount for row in plan.placeholders}


def test_us_plan_copies_the_rate_card_and_leaves_every_other_price_open() -> None:
    plan = build_plan("us", US_CARD)
    assert plan.currency == "USD" and plan.has_rate_card
    assert [(r.category, r.unit, r.rate, r.quantity_label) for r in plan.labor] == [
        ("Cloud Engineer", "hour", Decimal("165.00"), "Hours"),
        ("Solutions Architect", "hour", Decimal("210.50"), "Hours"),
    ]
    assert all(r.quantity_placeholder == "[NEEDS INPUT: hours]" for r in plan.labor)
    assert all(r.placeholder is None for r in plan.labor)  # the rate came from the card
    # every non-labour money cell is a placeholder and nothing else
    assert set(plan.amounts()) == {NEEDS_PRICE}
    assert list(_placeholders(plan)) == [
        "travel_odc",
        "materials_licences",
        "cloud_hosting",
        "subcontractors",
        "training",
        "transition",
    ]
    assert plan.warnings == []


def test_india_plan_prices_man_months_and_adds_gst_emd_and_fee_placeholders() -> None:
    plan = build_plan("in", IN_CARD, emd_amount=Decimal("250000"), tender_fee=Decimal("1000"))
    assert plan.currency == "INR"
    assert [r.quantity_label for r in plan.labor] == ["Man-months", "Man-months"]
    assert plan.labor[0].quantity_placeholder == "[NEEDS INPUT: man-months]"
    assert plan.labor[0].rate == Decimal("280000")

    holders = {row.key: row for row in plan.placeholders}
    assert {"emd", "tender_fee", "bank_guarantee_charges", "gst"} <= set(holders)
    assert holders["gst"].group == "tax" and holders["gst"].amount == NEEDS_PRICE
    assert "18%" in (holders["gst"].note or "") and "confirm" in (holders["gst"].note or "")
    assert holders["emd"].group == "fee"
    # the notice's amounts are quoted as context in the note, never written into the cell
    assert "₹2,50,000" in (holders["emd"].note or "") and holders["emd"].amount == NEEDS_PRICE
    assert "₹1,000" in (holders["tender_fee"].note or "")
    assert set(plan.amounts()) == {NEEDS_PRICE}
    # no stated amounts: the note simply says nothing about them
    bare = build_plan("in", IN_CARD)
    bare_holders = {row.key: row for row in bare.placeholders}
    assert "₹" not in (bare_holders["emd"].note or "")
    assert bare_holders["tender_fee"].note is None


def test_summary_sums_every_block_and_never_writes_a_number() -> None:
    plan = build_plan("in", IN_CARD)
    assert [(r.label, r.formula) for r in plan.summary] == [
        ("Labour subtotal", "=SUM(Labor!F2:F3)"),
        ("Other direct costs", "=SUM(Placeholders!C2:C7)"),
        ("Bid costs (EMD, fees)", "=SUM(Placeholders!C8:C10)"),
        ("Taxes", "=SUM(Placeholders!C11:C11)"),
        ("Total", "=SUM(B2:B5)"),
    ]
    us = build_plan("us", US_CARD)
    assert [r.label for r in us.summary] == ["Labour subtotal", "Other direct costs", "Total"]
    assert us.summary[-1].formula == "=SUM(B2:B3)"
    assert all(row.formula.startswith("=SUM(") for row in us.summary)


def test_an_empty_rate_card_gives_a_placeholder_row_and_a_warning() -> None:
    plan = build_plan("in", [])
    assert not plan.has_rate_card
    assert [(r.category, r.rate, r.placeholder) for r in plan.labor] == [
        (NEEDS_CATEGORY, None, NEEDS_PRICE)
    ]
    assert plan.labor[0].quantity_label == "Man-months"  # India defaults to man-months
    assert plan.currency == "INR"
    assert plan.warnings == ["the profile has no rate card, so every labour rate is a placeholder"]
    assert set(plan.amounts()) == {NEEDS_PRICE}
    assert build_plan("us", []).labor[0].quantity_label == "Hours"


def test_currency_and_unit_edge_cases() -> None:
    # the rate card's own currency wins over the region default
    assert build_plan("us", IN_CARD).currency == "INR"
    # an explicit currency wins over both, and a mixed card is flagged, not converted
    mixed = build_plan("us", [*US_CARD, IN_CARD[0]], currency="USD")
    assert mixed.currency == "USD"
    assert len(mixed.warnings) == 1 and "mixes currencies" in mixed.warnings[0]
    assert "INR" in mixed.warnings[0] and "USD" in mixed.warnings[0]
    assert [r.currency for r in mixed.labor if r.category == "Project Manager"] == ["INR"]
    # a unit outside the known set still gets a quantity placeholder
    odd = build_plan("us", [RateCardRow("Analyst", "week", Decimal("1"), "USD")])
    assert odd.labor[0].quantity_label == "Quantity"
    assert odd.labor[0].quantity_placeholder == "[NEEDS INPUT: quantity]"
    with pytest.raises(ValueError):
        build_plan("eu", US_CARD)
