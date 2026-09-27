"""M1-07: SBA size standards table and small-business status per NAICS."""

from decimal import Decimal

import pytest
from app.core.eligibility import (
    SizeStatus,
    determine_size,
    size_status_by_naics,
    small_business_status,
)
from app.core.reference import SizeStandard, sba_size_standards, size_standard


def test_bundled_table_shape() -> None:
    table = sba_size_standards()
    assert len(table) >= 900
    for std in table.values():
        assert len(std.naics) == 6 and std.naics.isdigit()
        assert (std.receipts_millions is None) != (std.employees is None), std
    assert size_standard("541511") == SizeStandard(
        "541511", "Custom Computer Programming Services", Decimal("34"), None, None
    )
    assert size_standard("541511").basis == "receipts"  # type: ignore[union-attr]
    assert size_standard("336411").employees == 1500  # aircraft manufacturing
    assert size_standard("336411").basis == "employees"  # type: ignore[union-attr]
    assert size_standard("541330").receipts_millions == Decimal("25.5")
    assert any(std.footnote for std in table.values()), "footnote column must be carried"
    assert size_standard("522110") is None  # asset-based (commercial banking) not bundled
    assert size_standard("000000") is None


def test_receipts_based_code() -> None:
    assert small_business_status("541511", avg_receipts=Decimal("34000000")) is SizeStatus.SMALL
    assert (
        small_business_status("541511", avg_receipts=Decimal("34000000.01"))
        is SizeStatus.OTHER_THAN_SMALL
    )
    assert small_business_status("541511", avg_receipts=0) is SizeStatus.SMALL
    # employees are irrelevant for a receipts code
    assert (
        small_business_status("541511", avg_receipts=1_000_000, employees=100_000)
        is SizeStatus.SMALL
    )
    det = determine_size("541511", avg_receipts=Decimal("40000000"))
    assert det.status is SizeStatus.OTHER_THAN_SMALL and det.basis == "receipts"
    assert det.threshold == Decimal("34000000") and det.measured == Decimal("40000000")
    assert det.as_dict()["threshold"] == "34000000"


def test_employee_based_code() -> None:
    assert small_business_status("336411", employees=1500) is SizeStatus.SMALL
    assert small_business_status("336411", employees=1501) is SizeStatus.OTHER_THAN_SMALL
    assert (
        small_business_status("336411", avg_receipts=Decimal("1"), employees=None)
        is SizeStatus.UNKNOWN
    )
    det = determine_size("336411", employees=10)
    assert det.basis == "employees" and det.threshold == 1500 and det.measured == 10


def test_missing_data_and_unknown_codes() -> None:
    assert small_business_status("541511") is SizeStatus.UNKNOWN
    assert small_business_status("541511", employees=5) is SizeStatus.UNKNOWN
    assert determine_size("541511").reason == "average receipts unknown"
    assert small_business_status("522110", avg_receipts=1, employees=1) is SizeStatus.UNKNOWN
    assert small_business_status("999999", avg_receipts=1, employees=1) is SizeStatus.UNKNOWN
    assert determine_size("999999").reason == "no SBA size standard"
    with pytest.raises(ValueError):
        determine_size("541511", avg_receipts=-1)
    with pytest.raises(ValueError):
        determine_size("336411", employees=-1)


def test_status_by_naics() -> None:
    out = size_status_by_naics(["541511", "336411", "999999"], Decimal("50000000"), 200)
    assert {k: v.status.value for k, v in out.items()} == {
        "541511": "other_than_small",
        "336411": "small",
        "999999": "unknown",
    }
    assert size_status_by_naics([], None, None) == {}
