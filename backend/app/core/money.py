"""Currency normalisation (SPEC 5.3: values kept in the source currency plus USD)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal


def to_usd(
    amount: Decimal | None, currency: str | None, rates: Mapping[str, float]
) -> Decimal | None:
    """Convert with FX_RATES (currency -> USD per unit); None when the rate is unknown."""
    if amount is None or not currency:
        return None
    rate = rates.get(currency.upper())
    if rate is None:
        return None
    return (amount * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
