"""Bundled reference tables (app/core/data), loaded once per process. Pure file reads."""

from __future__ import annotations

import csv
from functools import lru_cache
from importlib import resources
from typing import IO

NAICS_FILE = "naics_2022.csv"


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
