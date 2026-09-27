"""M3-01: INR/USD parsing and formatting (lakh/crore grouping) and configurable FX (core.money)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.config import Settings
from app.core.finance import Money
from app.core.finance import to_usd as finance_to_usd
from app.core.money import (
    CRORE,
    LAKH,
    Conversion,
    format_inr,
    format_money,
    format_usd,
    group_indian,
    parse_amount,
    parse_inr,
    parse_usd,
    to_usd,
)

D = Decimal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # SPEC 12 examples
        ("₹12,50,000", "1250000"),
        ("Rs. 1.2 Cr", "12000000"),
        ("12.5 Lakh", "1250000"),
        ("1,20,000/-", "120000"),
        ("INR 5 crore", "50000000"),
        # variants seen on CPPP / GeM / GePNIC pages
        ("₹ 5,00,000.00", "500000.00"),
        ("Rs 50 lakhs", "5000000"),
        ("Rs.50,000/-", "50000"),
        ("INR 2.5 Crores", "25000000"),
        ("1.5 lac", "150000"),
        ("3 Lacs", "300000"),
        ("75 L", "7500000"),
        ("2 cr", "20000000"),
        ("Rs. 10 Thousand", "10000"),
        ("1,00,000.50", "100000.50"),
        ("100000", "100000"),
        ("₹1,234,567", "1234567"),  # western grouping still parses
        ("Rs. 0", "0"),
        ("INR 12.50", "12.50"),
        ("EMD: ₹ 25,000/- (Rupees Twenty Five Thousand only)", "25000"),
        ("Tender Value ₹12,50,000 excluding GST", "1250000"),
        ("1.234567 lakh", "123456.7"),  # fractional result keeps only significant digits
    ],
)
def test_parse_inr(text: str, expected: str) -> None:
    value = parse_inr(text)
    assert value is not None
    assert isinstance(value, Decimal)
    assert value == D(expected)
    assert str(value) == expected  # scale preserved, no float noise


@pytest.mark.parametrize(
    "text",
    ["", "   ", "Nil", "Rs.", "₹", "$1,000", "USD 5", "1.2.3", "as per tender", None],
)
def test_parse_inr_unparseable(text: str | None) -> None:
    assert parse_inr(text) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", "1234.56"),
        ("USD 1.2M", "1200000"),
        ("$2K", "2000"),
        ("$3 billion", "3000000000"),
        ("1.5 million", "1500000"),
        ("US$ 750,000", "750000"),
        ("$0.99", "0.99"),
        ("Award ceiling: $5,000,000.00", "5000000.00"),
    ],
)
def test_parse_usd(text: str, expected: str) -> None:
    value = parse_usd(text)
    assert value is not None and value == D(expected) and str(value) == expected


@pytest.mark.parametrize("text", ["", "₹5", "Rs 5", "INR 5", "TBD", "5 lakh"])
def test_parse_usd_unparseable(text: str) -> None:
    assert parse_usd(text) is None


def test_parse_amount_dispatches_on_currency() -> None:
    assert parse_amount("₹1 lakh", "INR") == D("100000")
    assert parse_amount("$1K", "USD") == D("1000")
    with pytest.raises(ValueError):
        parse_amount("1", "EUR")


@pytest.mark.parametrize(
    ("digits", "expected"),
    [
        ("0", "0"),
        ("999", "999"),
        ("1000", "1,000"),
        ("99999", "99,999"),
        ("100000", "1,00,000"),
        ("1250000", "12,50,000"),
        ("10000000", "1,00,00,000"),
        ("123456789012", "1,23,45,67,89,012"),
    ],
)
def test_group_indian(digits: str, expected: str) -> None:
    assert group_indian(digits) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("1250000", "₹12,50,000"),
        ("1250000.00", "₹12,50,000"),
        ("1250000.50", "₹12,50,000.50"),
        ("1250000.5", "₹12,50,000.50"),
        ("1250000.005", "₹12,50,000.01"),  # half-up to paise
        ("100000", "₹1,00,000"),
        ("99999", "₹99,999"),
        ("0", "₹0"),
        ("12000000", "₹1,20,00,000"),
        ("-1250000", "-₹12,50,000"),
    ],
)
def test_format_inr_full(amount: str, expected: str) -> None:
    assert format_inr(D(amount)) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("12000000", "₹1.2 Cr"),
        ("10000000", "₹1 Cr"),
        ("12345678", "₹1.23 Cr"),
        ("125000000", "₹12.5 Cr"),
        ("1250000", "₹12.5 Lakh"),
        ("100000", "₹1 Lakh"),
        ("99999", "₹99,999"),
        ("2500", "₹2,500"),
        ("0", "₹0"),
        ("-12000000", "-₹1.2 Cr"),
    ],
)
def test_format_inr_compact(amount: str, expected: str) -> None:
    assert format_inr(D(amount), compact=True) == expected


def test_format_inr_round_trips_through_parse_inr() -> None:
    for raw in ("1250000", "12000000", "99999", "1250000.50"):
        full = format_inr(D(raw))
        assert parse_inr(full) == D(raw)
    assert parse_inr(format_inr(D("12000000"), compact=True)) == D("12000000")


def test_format_usd() -> None:
    assert format_usd(D("1234.5")) == "$1,234.50"
    assert format_usd(D("1234")) == "$1,234"
    assert format_usd(D("-1234.56")) == "-$1,234.56"
    assert format_usd(D("1200000"), compact=True) == "$1.2M"
    assert format_usd(D("2500"), compact=True) == "$2.5K"
    assert format_usd(D("3000000000"), compact=True) == "$3B"
    assert format_usd(D("999"), compact=True) == "$999"


def test_format_money_by_currency() -> None:
    assert format_money(D("1250000"), "INR") == "₹12,50,000"
    assert format_money(D("1250000"), "usd") == "$1,250,000"
    assert format_money(D("1250000"), "INR", compact=True) == "₹12.5 Lakh"
    with pytest.raises(ValueError):
        format_money(D("1"), "EUR")


def test_constants() -> None:
    assert str(LAKH) == "100000"
    assert str(CRORE) == "10000000"
    assert LAKH * 100 == CRORE


def test_to_usd_uses_explicit_table_and_records_rate() -> None:
    rates = {"USD": 1.0, "INR": 0.0119}
    result = to_usd(D("1000000"), "INR", rates)
    assert result == Conversion(usd=D("11900.00"), rate=D("0.0119"), currency="INR")
    assert isinstance(result.rate, Decimal) and str(result.rate) == "0.0119"
    same = to_usd(D("42.005"), "usd", rates)
    assert same.usd == D("42.01") and same.rate == D("1") and same.currency == "USD"
    with pytest.raises(ValueError, match="no USD rate for USD"):
        to_usd(D("1"), "USD", {})  # the table is the source of truth, even for USD


def test_to_usd_defaults_to_settings_fx_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import money

    monkeypatch.setattr(
        money, "get_settings", lambda: Settings(_env_file=None, fx_rates='{"INR": 0.02}')
    )
    assert to_usd(D("100"), "INR").usd == D("2.00")


def test_to_usd_unknown_currency_and_bad_inputs() -> None:
    with pytest.raises(ValueError, match="no USD rate for EUR"):
        to_usd(D("1"), "EUR", {"USD": 1})
    with pytest.raises(ValueError):
        to_usd(D("1"), "INR", {"INR": 0})
    with pytest.raises(ValueError):
        to_usd(D("1"), "INR", {"INR": -0.01})


def test_fx_rates_setting_parses_json_env() -> None:
    settings = Settings(_env_file=None, fx_rates='{"USD": 1.0, "INR": 0.0119}')
    assert settings.fx_rates == {"USD": 1.0, "INR": 0.0119}
    assert "INR" in Settings(_env_file=None).fx_rates


def test_finance_to_usd_delegates_to_money() -> None:
    assert finance_to_usd(Money(D("1000"), "INR"), {"INR": 0.0119}) == D("11.90")
