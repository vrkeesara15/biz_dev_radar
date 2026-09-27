"""Bundled reference tables (app/core/data), loaded once per process. Pure file reads."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import IO

NAICS_FILE = "naics_2022.csv"
SBA_FILE = "sba_size_standards.csv"


@dataclass(frozen=True, slots=True)
class SizeStandard:
    """One SBA size standard: either a receipts cap (USD millions) or an employee cap."""

    naics: str
    title: str
    receipts_millions: Decimal | None
    employees: int | None
    footnote: str | None

    @property
    def basis(self) -> str:
        return "receipts" if self.receipts_millions is not None else "employees"


def _open(name: str) -> IO[str]:
    return resources.files("app.core.data").joinpath(name).open("r", encoding="utf-8")


@lru_cache(maxsize=1)
def naics_codes() -> dict[str, str]:
    """2022 NAICS 6-digit code -> title."""
    with _open(NAICS_FILE) as handle:
        rows = csv.DictReader(handle)
        return {row["code"].strip(): row["title"].strip() for row in rows}


def is_valid_naics(code: str) -> bool:
    return code in naics_codes()


def naics_title(code: str) -> str | None:
    return naics_codes().get(code)


@lru_cache(maxsize=1)
def sba_size_standards() -> dict[str, SizeStandard]:
    """NAICS -> SBA size standard (effective 2023-03-17)."""
    out: dict[str, SizeStandard] = {}
    with _open(SBA_FILE) as handle:
        for row in csv.DictReader(handle):
            receipts = row["receipts_millions"].strip()
            employees = row["employees"].strip()
            out[row["naics"].strip()] = SizeStandard(
                naics=row["naics"].strip(),
                title=row["title"].strip(),
                receipts_millions=Decimal(receipts) if receipts else None,
                employees=int(employees) if employees else None,
                footnote=row["footnote"].strip() or None,
            )
    return out


def size_standard(naics: str) -> SizeStandard | None:
    return sba_size_standards().get(naics)
