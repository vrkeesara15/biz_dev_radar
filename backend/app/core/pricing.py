"""Pricing template plan (SPEC 8 agent 7, SPEC 1: "pricing beyond a template with
placeholders" is out of scope). Pure: it lays out the workbook, it does not write one.

    plan = build_plan(region, rate_card, currency="INR", emd_amount=...)
    # plan.labor / plan.placeholders / plan.summary -> app.agents.pricing renders the XLSX

The one hard rule: a price appears only when the tenant's rate card supplies it. Every
other money cell -- quantities, other direct costs, GST, EMD, tender fee -- is a
[NEEDS INPUT: ...] placeholder for a human to fill in. Nothing is estimated, escalated or
extrapolated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.money import format_money

NEEDS_PRICE = "[NEEDS INPUT: price]"
NEEDS_CATEGORY = "[NEEDS INPUT: labor category]"
REGION_US = "us"
REGION_IN = "in"
DEFAULT_CURRENCY = {REGION_US: "USD", REGION_IN: "INR"}

GROUP_COST = "cost"
GROUP_FEE = "fee"
GROUP_TAX = "tax"

# quantity column per rate unit; India prices people by the man-month (SPEC 8 agent 7)
UNIT_QUANTITY: dict[str, str] = {
    "hour": "Hours",
    "day": "Days",
    "month": "Man-months",
}
DEFAULT_UNIT = {REGION_US: "hour", REGION_IN: "month"}

SHEET_LABOR = "Labor"
SHEET_PLACEHOLDERS = "Placeholders"
SHEET_SUMMARY = "Summary"

LABOR_HEADERS = ("Labor category", "Unit", "Rate", "Currency", "Quantity", "Extended price")
PLACEHOLDER_HEADERS = ("Item", "Basis", "Amount", "Note")
SUMMARY_HEADERS = ("Line", "Amount")

# (key, label, basis, note) -- non-labour costs every bid has to price by hand
COST_LINES: tuple[tuple[str, str, str, str | None], ...] = (
    ("travel_odc", "Travel and other direct costs", "per the trip plan", None),
    ("materials_licences", "Materials, licences and subscriptions", "per unit", None),
    ("cloud_hosting", "Cloud / hosting", "per month", None),
    ("subcontractors", "Subcontractor and teaming costs", "per subcontract", None),
    ("training", "Training and knowledge transfer", "lump sum", None),
    ("transition", "Transition in / out", "lump sum", None),
)
IN_FEE_LINES: tuple[tuple[str, str, str, str | None], ...] = (
    (
        "emd",
        "Earnest money deposit (EMD)",
        "as the tender states",
        "Refundable; confirm the instrument (DD, BG or online) and the validity demanded.",
    ),
    ("tender_fee", "Tender document fee", "as the tender states", None),
    (
        "bank_guarantee_charges",
        "Bank guarantee charges",
        "bank quote",
        "Charged by the issuing bank for the EMD or performance BG.",
    ),
)
IN_TAX_LINES: tuple[tuple[str, str, str, str | None], ...] = (
    (
        "gst",
        "GST",
        "on the taxable value",
        "The standard rate is 18%; confirm the applicable rate and the SAC code before quoting.",
    ),
)


@dataclass(frozen=True, slots=True)
class RateCardRow:
    """One rate_card_entries row, as the plan sees it."""

    labor_category: str
    unit: str
    rate_amount: Decimal
    rate_currency: str
    min_years_experience: int | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class LaborRow:
    category: str
    unit: str
    quantity_label: str
    rate: Decimal | None  # None -> the rate cell holds `placeholder`
    currency: str | None
    min_years_experience: int | None = None
    notes: str | None = None
    placeholder: str | None = None

    @property
    def quantity_placeholder(self) -> str:
        return f"[NEEDS INPUT: {self.quantity_label.lower()}]"


@dataclass(frozen=True, slots=True)
class PlaceholderRow:
    key: str
    label: str
    basis: str
    group: str  # cost | fee | tax
    amount: str = NEEDS_PRICE
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryRow:
    label: str
    formula: str


@dataclass(frozen=True, slots=True)
class PricingPlan:
    region: str
    currency: str
    labor: list[LaborRow] = field(default_factory=list)
    placeholders: list[PlaceholderRow] = field(default_factory=list)
    summary: list[SummaryRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_rate_card(self) -> bool:
        return any(row.rate is not None for row in self.labor)

    def amounts(self) -> list[str]:
        """Every money cell that is not a rate-card rate. All must be placeholders."""
        return [row.amount for row in self.placeholders] + [
            row.placeholder for row in self.labor if row.placeholder is not None
        ]


def _quantity_label(unit: str) -> str:
    return UNIT_QUANTITY.get(unit, "Quantity")


def _labor_rows(region: str, rate_card: list[RateCardRow]) -> tuple[list[LaborRow], list[str]]:
    if not rate_card:
        unit = DEFAULT_UNIT[region]
        return (
            [
                LaborRow(
                    category=NEEDS_CATEGORY,
                    unit=unit,
                    quantity_label=_quantity_label(unit),
                    rate=None,
                    currency=None,
                    placeholder=NEEDS_PRICE,
                )
            ],
            ["the profile has no rate card, so every labour rate is a placeholder"],
        )
    rows = [
        LaborRow(
            category=entry.labor_category,
            unit=entry.unit,
            quantity_label=_quantity_label(entry.unit),
            rate=entry.rate_amount,
            currency=entry.rate_currency,
            min_years_experience=entry.min_years_experience,
            notes=entry.notes,
        )
        for entry in sorted(rate_card, key=lambda e: e.labor_category.lower())
    ]
    return rows, []


def _placeholder_rows(
    region: str, *, emd_amount: Decimal | None, tender_fee: Decimal | None, currency: str
) -> list[PlaceholderRow]:
    rows = [
        PlaceholderRow(key=key, label=label, basis=basis, group=GROUP_COST, note=note)
        for key, label, basis, note in COST_LINES
    ]
    if region != REGION_IN:
        return rows
    stated = {"emd": emd_amount, "tender_fee": tender_fee}
    for key, label, basis, note in IN_FEE_LINES:
        amount = stated.get(key)
        extra = (
            None
            if amount is None
            else f"The notice states {format_money(Decimal(amount), currency)}."
        )
        rows.append(
            PlaceholderRow(
                key=key,
                label=label,
                basis=basis,
                group=GROUP_FEE,
                note=" ".join(part for part in (extra, note) if part) or None,
            )
        )
    rows.extend(
        PlaceholderRow(key=key, label=label, basis=basis, group=GROUP_TAX, note=note)
        for key, label, basis, note in IN_TAX_LINES
    )
    return rows


def _range(sheet: str, column: str, first: int, last: int) -> str:
    return f"{sheet}!{column}{first}:{column}{last}"


def _summary_rows(labor: list[LaborRow], placeholders: list[PlaceholderRow]) -> list[SummaryRow]:
    """SUM over every block. Placeholder cells hold text, and SUM ignores text, so the
    workbook totals zero until a human types real numbers -- it never guesses one."""
    rows = [
        SummaryRow(
            "Labour subtotal",
            f"=SUM({_range(SHEET_LABOR, 'F', 2, len(labor) + 1)})",
        )
    ]
    groups = [
        (GROUP_COST, "Other direct costs"),
        (GROUP_FEE, "Bid costs (EMD, fees)"),
        (GROUP_TAX, "Taxes"),
    ]
    for group, label in groups:
        indexes = [i for i, row in enumerate(placeholders, start=2) if row.group == group]
        if not indexes:
            continue
        rows.append(
            SummaryRow(
                label,
                f"=SUM({_range(SHEET_PLACEHOLDERS, 'C', min(indexes), max(indexes))})",
            )
        )
    rows.append(SummaryRow("Total", f"=SUM(B2:B{len(rows) + 1})"))
    return rows


def build_plan(
    region: str,
    rate_card: list[RateCardRow],
    *,
    currency: str | None = None,
    emd_amount: Decimal | None = None,
    tender_fee: Decimal | None = None,
) -> PricingPlan:
    """The workbook plan for one pursuit. `currency` defaults to the rate card's, then to
    the region's."""
    if region not in DEFAULT_CURRENCY:
        raise ValueError(f"unknown region {region!r}")
    labor, warnings = _labor_rows(region, rate_card)
    resolved = currency or (rate_card[0].rate_currency if rate_card else DEFAULT_CURRENCY[region])
    mixed = {row.currency for row in labor if row.currency} - {resolved}
    if mixed:
        warnings.append(
            "the rate card mixes currencies ("
            + ", ".join(sorted({resolved, *mixed}))
            + "); each rate keeps its own, so convert before totalling"
        )
    placeholders = _placeholder_rows(
        region, emd_amount=emd_amount, tender_fee=tender_fee, currency=resolved
    )
    return PricingPlan(
        region=region,
        currency=resolved,
        labor=labor,
        placeholders=placeholders,
        summary=_summary_rows(labor, placeholders),
        warnings=warnings,
    )
