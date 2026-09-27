"""Dashboard KPI arithmetic (SPEC 9, 10.4 screen 2). Pure, no I/O.

    win_rate(awarded=3, lost=1)            -> 0.75
    value_pair(Decimal("1000000"), rates)  -> {"USD": 1000000.00, "INR": 83000000.00}
    hours_saved(submitted=4, per_package=20) -> 80

Every ratio answers None rather than 0 when its denominator is empty: "no win rate yet"
and "we lose everything" must not look the same on the home screen.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
USD = "USD"
INR = "INR"

# SPEC 9 asks for "average hours saved per package". Nothing measures it, so it is a
# configured ESTIMATE (HOURS_SAVED_PER_PACKAGE) multiplied by the packages submitted, and
# the API says so in `hours_saved_basis`.
DEFAULT_HOURS_SAVED_PER_PACKAGE = 20
HOURS_SAVED_BASIS = (
    "estimate: HOURS_SAVED_PER_PACKAGE hours per submitted package, not a measurement"
)


def ratio(numerator: int, denominator: int) -> float | None:
    """A rate in 0..1, or None when nothing has happened yet."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def win_rate(*, awarded: int, lost: int) -> float | None:
    """SPEC 9: awarded / (awarded + lost). Pursuits still in flight do not count."""
    return ratio(awarded, awarded + lost)


def alert_precision(*, useful: int, rated: int) -> float | None:
    """Share of rated alerts a human called useful (match_feedback, M4)."""
    return ratio(useful, rated)


def hours_saved(*, submitted: int, per_package: int = DEFAULT_HOURS_SAVED_PER_PACKAGE) -> int:
    return max(0, submitted) * max(0, per_package)


def usd_to(amount: Decimal, currency: str, rates: Mapping[str, float | int | str]) -> Decimal:
    """Convert a USD figure into `currency` with the same table core.money reads the other
    way (currency -> USD), so the two directions can never disagree."""
    code = currency.strip().upper()
    if code == USD:
        return Decimal(amount).quantize(CENTS, rounding=ROUND_HALF_UP)
    if code not in rates:
        raise ValueError(f"no USD rate for {code}")
    rate = Decimal(str(rates[code]))
    if rate <= 0:
        raise ValueError(f"invalid USD rate for {code}: {rate}")
    return (Decimal(amount) / rate).quantize(CENTS, rounding=ROUND_HALF_UP)


def value_pair(usd: Decimal | None, rates: Mapping[str, float | int | str]) -> dict[str, Decimal]:
    """SPEC 9 wants pipeline value "in USD and INR". Both come from the SAME USD figure
    (`opportunities.estimated_value_*_usd`), so the two columns always agree."""
    amount = Decimal(usd or 0)
    try:
        inr = usd_to(amount, INR, rates)
    except ValueError:
        inr = Decimal("0.00")
    return {USD: usd_to(amount, USD, rates), INR: inr}
