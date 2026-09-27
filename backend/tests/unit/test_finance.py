"""M1-02: average of the last 3 FY turnover with currency preserved (core.finance)."""

from decimal import Decimal

import pytest
from app.core.finance import (
    AverageTurnover,
    FiscalYearRevenue,
    MixedCurrencyError,
    Money,
    average_turnover,
    in_millions,
    latest_fiscal_years,
    to_usd,
)


def fy(year: int, amount: str, currency: str = "INR") -> FiscalYearRevenue:
    return FiscalYearRevenue(year, Decimal(amount), currency)


def test_average_of_last_three_fiscal_years_keeps_currency() -> None:
    avg = average_turnover([fy(2023, "100"), fy(2024, "200"), fy(2025, "330")])
    assert avg == AverageTurnover(Decimal("210.00"), "INR", (2023, 2024, 2025))
    assert avg.years_used == 3


def test_picks_latest_three_from_a_longer_history_regardless_of_order() -> None:
    entries = [fy(2025, "3"), fy(2021, "1000"), fy(2023, "1"), fy(2024, "2"), fy(2022, "500")]
    avg = average_turnover(entries)
    assert avg is not None
    assert avg.fiscal_years == (2023, 2024, 2025) and avg.amount == Decimal("2.00")
    assert [e.fiscal_year for e in latest_fiscal_years(entries, 2)] == [2025, 2024]
    assert average_turnover(entries, years=5) is not None
    assert average_turnover(entries, years=5).fiscal_years == (2021, 2022, 2023, 2024, 2025)  # type: ignore[union-attr]


def test_fewer_years_average_over_available_and_report_them() -> None:
    avg = average_turnover([fy(2025, "1000000.00", "USD"), fy(2024, "500000.50", "USD")])
    assert avg == AverageTurnover(Decimal("750000.25"), "USD", (2024, 2025))
    single = average_turnover([fy(2025, "42.005", "USD")])
    assert single is not None and single.amount == Decimal("42.01")  # half-up cents


def test_duplicate_year_keeps_the_last_entry() -> None:
    avg = average_turnover([fy(2025, "1"), fy(2025, "9")])
    assert avg is not None and avg.amount == Decimal("9.00") and avg.fiscal_years == (2025,)


def test_empty_and_mixed_currency() -> None:
    assert average_turnover([]) is None
    with pytest.raises(MixedCurrencyError, match="INR, USD"):
        average_turnover([fy(2025, "1", "USD"), fy(2024, "1", "INR")])
    # a mixed currency outside the 3-year window does not matter
    history = [
        fy(2025, "1", "USD"),
        fy(2024, "1", "USD"),
        fy(2023, "1", "USD"),
        fy(2010, "1", "INR"),
    ]
    assert average_turnover(history) == AverageTurnover(Decimal("1.00"), "USD", (2023, 2024, 2025))
    with pytest.raises(ValueError):
        latest_fiscal_years([], 0)


def test_money_conversion_and_millions() -> None:
    assert to_usd(Money(Decimal("1000"), "INR"), {"USD": 1.0, "INR": 0.012}) == Decimal("12.00")
    assert to_usd(Money(Decimal("5"), "USD"), {"USD": 1}) == Decimal("5.00")
    with pytest.raises(ValueError):
        to_usd(Money(Decimal("5"), "USD"), {"INR": 0.012})
    with pytest.raises(ValueError):
        Money(Decimal("1"), "EUR")
    assert in_millions(Decimal("12345678")) == Decimal("12.3457")
    assert in_millions(Decimal("2250000")) == Decimal("2.2500")
