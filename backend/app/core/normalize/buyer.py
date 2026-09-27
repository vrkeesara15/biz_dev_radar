"""Buyer-name normaliser for dedupe (SPEC 5.4: same buyer across sources).

    normalized_buyer("Tata Consultancy Services Pvt. Ltd.") == "tata consultancy services"
    normalized_buyer("The Boeing Company") == "boeing"

Lower-case, punctuation removed, a leading article and trailing legal-form suffixes
dropped, whitespace collapsed. A name that consists only of a suffix word survives.
"""

from __future__ import annotations

import re

LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "lp",
        "ltd",
        "limited",
        "pvt",
        "private",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "sa",
        "ag",
        "nv",
        "bv",
        "pte",
        "opc",
    }
)
_ARTICLES = frozenset({"the"})
_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def normalized_buyer(value: str | None) -> str | None:
    if not value:
        return None
    # "L.L.C." / "U.S." -> "llc" / "us": dots join abbreviation letters, they never separate words
    words = [w for w in _PUNCT_RE.split(value.lower().replace(".", "")) if w]
    if not words:
        return None
    if len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or None
