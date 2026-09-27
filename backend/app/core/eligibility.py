"""Eligibility rules (SPEC 4.2, 6 stage 2). Pure.

    small_business_status("541511", avg_receipts=Decimal("20000000"), employees=120)
    -> SizeStatus.SMALL   (541511 cap is $34M receipts)

Receipts are average annual receipts in USD (13 CFR 121.104); employees the average
head count (13 CFR 121.106). Missing data for the code's basis -> "unknown"; a NAICS
without a bundled standard (exceptions, asset-based) -> "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.core.finance import MILLION
from app.core.reference import SizeStandard, size_standard


class SizeStatus(StrEnum):
    SMALL = "small"
    OTHER_THAN_SMALL = "other_than_small"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SizeDetermination:
    naics: str
    status: SizeStatus
    basis: str | None  # receipts | employees | None
    threshold: Decimal | int | None  # USD (receipts) or head count
    measured: Decimal | int | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "naics": self.naics,
            "status": self.status.value,
            "basis": self.basis,
            "threshold": None if self.threshold is None else str(self.threshold),
            "measured": None if self.measured is None else str(self.measured),
            "reason": self.reason,
        }


def determine_size(
    naics: str,
    avg_receipts: Decimal | int | float | None = None,
    employees: int | None = None,
    *,
    standard: SizeStandard | None = None,
) -> SizeDetermination:
    """Full determination with the threshold and the value compared against it."""
    code = naics.strip()
    std = standard or size_standard(code)
    if std is None:
        return SizeDetermination(code, SizeStatus.UNKNOWN, None, None, None, "no SBA size standard")
    if std.receipts_millions is not None:
        cap = std.receipts_millions * MILLION
        if avg_receipts is None:
            return SizeDetermination(
                code, SizeStatus.UNKNOWN, "receipts", cap, None, "average receipts unknown"
            )
        measured = Decimal(str(avg_receipts))
        if measured < 0:
            raise ValueError("avg_receipts must be >= 0")
        status = SizeStatus.SMALL if measured <= cap else SizeStatus.OTHER_THAN_SMALL
        return SizeDetermination(
            code, status, "receipts", cap, measured, f"receipts vs ${std.receipts_millions}M cap"
        )
    assert std.employees is not None
    if employees is None:
        return SizeDetermination(
            code, SizeStatus.UNKNOWN, "employees", std.employees, None, "employee count unknown"
        )
    if employees < 0:
        raise ValueError("employees must be >= 0")
    status = SizeStatus.SMALL if employees <= std.employees else SizeStatus.OTHER_THAN_SMALL
    return SizeDetermination(
        code, status, "employees", std.employees, employees, f"employees vs {std.employees} cap"
    )


def small_business_status(
    naics: str,
    avg_receipts: Decimal | int | float | None = None,
    employees: int | None = None,
) -> SizeStatus:
    """small | other_than_small | unknown for one NAICS code."""
    return determine_size(naics, avg_receipts, employees).status


def size_status_by_naics(
    codes: list[str],
    avg_receipts: Decimal | int | float | None,
    employees: int | None,
) -> dict[str, SizeDetermination]:
    return {code: determine_size(code, avg_receipts, employees) for code in codes}
