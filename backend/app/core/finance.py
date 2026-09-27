"""Money helpers for profile finance (SPEC 4.2). Pure.

    avg = average_turnover([FiscalYearRevenue(2025, Decimal("120"), "INR"), ...])
    avg.amount, avg.currency, avg.fiscal_years      # currency preserved, never converted

Indian tenders usually ask for the average of the last 3 FYs; SBA receipts use the same
window (13 CFR 121.104 uses 5 for receipts since 2022, `years=` is a parameter).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CURRENCIES = ("USD", "INR")
CENTS = Decimal("0.01")
MILLION = Decimal("1000000")


class MixedCurrencyError(ValueError):
    """Turnover figures in different currencies cannot be averaged."""


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in CURRENCIES:
            raise ValueError(f"unsupported currency {self.currency!r}")


@dataclass(frozen=True, slots=True)
class FiscalYearRevenue:
    fiscal_year: int
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class AverageTurnover:
    amount: Decimal
    currency: str
    fiscal_years: tuple[int, ...]

    @property
    def years_used(self) -> int:
        return len(self.fiscal_years)


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def latest_fiscal_years(
    entries: Iterable[FiscalYearRevenue], years: int = 3
) -> list[FiscalYearRevenue]:
    """The most recent `years` distinct fiscal years (a duplicate year keeps the last given)."""
    if years <= 0:
        raise ValueError("years must be positive")
    by_year: dict[int, FiscalYearRevenue] = {}
    for entry in entries:
        by_year[entry.fiscal_year] = entry
    return [by_year[y] for y in sorted(by_year, reverse=True)[:years]]


def average_turnover(
    entries: Iterable[FiscalYearRevenue], years: int = 3
) -> AverageTurnover | None:
    """Mean of the latest `years` fiscal years, in their own currency. None without data.
    Fewer than `years` entries average over what exists (fiscal_years says which)."""
    window = latest_fiscal_years(entries, years)
    if not window:
        return None
    currencies = {e.currency for e in window}
    if len(currencies) != 1:
        raise MixedCurrencyError(
            "turnover entries use several currencies: " + ", ".join(sorted(currencies))
        )
    total = sum((Decimal(e.amount) for e in window), Decimal(0))
    return AverageTurnover(
        amount=quantize_money(total / len(window)),
        currency=window[0].currency,
        fiscal_years=tuple(sorted(e.fiscal_year for e in window)),
    )


def to_usd(money: Money, fx_rates: Mapping[str, float | Decimal]) -> Decimal:
    """Convert with a currency -> USD rate table (Settings.fx_rates)."""
    try:
        rate = Decimal(str(fx_rates[money.currency]))
    except KeyError as exc:
        raise ValueError(f"no USD rate for {money.currency}") from exc
    return quantize_money(money.amount * rate)


def in_millions(amount: Decimal) -> Decimal:
    return (Decimal(amount) / MILLION).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
