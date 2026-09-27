"""Dashboard KPI arithmetic (SPEC 9, M6-08). Pure."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.core.dashboard import (
    DEFAULT_HOURS_SAVED_PER_PACKAGE,
    HOURS_SAVED_BASIS,
    alert_precision,
    hours_saved,
    ratio,
    usd_to,
    value_pair,
    win_rate,
)

RATES = {"USD": 1.0, "INR": 0.012}


def test_a_rate_with_no_denominator_is_none_not_zero() -> None:
    assert win_rate(awarded=0, lost=0) is None
    assert alert_precision(useful=0, rated=0) is None
    assert ratio(0, 0) is None
    # losing everything is a real 0.0, not "no data"
    assert win_rate(awarded=0, lost=3) == 0.0
    assert alert_precision(useful=0, rated=5) == 0.0


def test_win_rate_counts_only_decided_pursuits() -> None:
    assert win_rate(awarded=3, lost=1) == 0.75
    assert win_rate(awarded=1, lost=2) == 0.3333
    assert win_rate(awarded=5, lost=0) == 1.0


def test_hours_saved_is_a_labelled_estimate() -> None:
    assert DEFAULT_HOURS_SAVED_PER_PACKAGE == 20
    assert "estimate" in HOURS_SAVED_BASIS
    assert hours_saved(submitted=4) == 80
    assert hours_saved(submitted=4, per_package=12) == 48
    assert hours_saved(submitted=0) == 0
    assert hours_saved(submitted=-3, per_package=-1) == 0


def test_usd_to_inverts_the_core_money_table() -> None:
    # core.money.to_usd multiplies by the rate, so the dashboard divides by it
    assert usd_to(Decimal("120"), "INR", RATES) == Decimal("10000.00")
    assert usd_to(Decimal("120"), "usd", RATES) == Decimal("120.00")
    with pytest.raises(ValueError, match="no USD rate for EUR"):
        usd_to(Decimal("1"), "EUR", RATES)
    with pytest.raises(ValueError, match="invalid USD rate"):
        usd_to(Decimal("1"), "INR", {"INR": 0})


def test_value_pair_always_agrees_with_itself() -> None:
    pair = value_pair(Decimal("1200.00"), RATES)
    assert pair["USD"] == Decimal("1200.00")
    assert pair["INR"] == Decimal("100000.00")
    assert value_pair(None, RATES) == {"USD": Decimal("0.00"), "INR": Decimal("0.00")}
    # an FX table without INR degrades to zero rather than raising on the home screen
    assert value_pair(Decimal("5"), {"USD": 1.0}) == {
        "USD": Decimal("5.00"),
        "INR": Decimal("0.00"),
    }
