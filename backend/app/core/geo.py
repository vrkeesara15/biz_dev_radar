"""Geography reference (SPEC 4.4): US state/territory codes and Indian states/UTs. Pure."""

from __future__ import annotations

import re
from collections.abc import Iterable

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}  # fmt: skip

# ISO 3166-2:IN codes for states and union territories.
INDIA_STATES_UTS: dict[str, str] = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CH": "Chandigarh", "CG": "Chhattisgarh",
    "DH": "Dadra and Nagar Haveli and Daman and Diu", "DL": "Delhi", "GA": "Goa", "GJ": "Gujarat",
    "HR": "Haryana", "HP": "Himachal Pradesh", "JK": "Jammu and Kashmir", "JH": "Jharkhand",
    "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh", "LD": "Lakshadweep", "MP": "Madhya Pradesh",
    "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram", "NL": "Nagaland",
    "OD": "Odisha", "PY": "Puducherry", "PB": "Punjab", "RJ": "Rajasthan", "SK": "Sikkim",
    "TN": "Tamil Nadu", "TS": "Telangana", "TR": "Tripura", "UP": "Uttar Pradesh",
    "UK": "Uttarakhand", "WB": "West Bengal",
}  # fmt: skip

_ISO2 = re.compile(r"^[A-Z]{2}$")


def normalize_codes(values: Iterable[str], allowed: dict[str, str] | None, label: str) -> list[str]:
    """Upper-case, de-duplicate (order kept) and check against `allowed` when given."""
    out: list[str] = []
    for raw in values:
        code = raw.strip().upper()
        if not _ISO2.match(code):
            raise ValueError(f"{label} {raw!r} must be a 2-letter code")
        if allowed is not None and code not in allowed:
            raise ValueError(f"unknown {label} {raw!r}")
        if code not in out:
            out.append(code)
    return out


def normalize_countries(values: Iterable[str]) -> list[str]:
    return normalize_codes(values, None, "country")


def normalize_us_states(values: Iterable[str]) -> list[str]:
    return normalize_codes(values, US_STATES, "US state")


def normalize_india_states(values: Iterable[str]) -> list[str]:
    return normalize_codes(values, INDIA_STATES_UTS, "Indian state/UT")


def normalize_names(values: Iterable[str], *, max_length: int = 200) -> list[str]:
    """Free-text lists (cities, buyers): trimmed, de-duplicated case-insensitively."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        name = " ".join(raw.split())
        if not name:
            continue
        if len(name) > max_length:
            raise ValueError(f"{name[:20]!r}... exceeds {max_length} characters")
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out
