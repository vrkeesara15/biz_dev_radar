"""Solicitation / tender reference normaliser for the cross-source dedupe key (SPEC 5.4).

    normalized_reference("RFP No. 47PF-0018-R0023") == "47PF0018R0023"
    normalized_reference("Tender No.: TS/2026/EDU-0042") == "TS2026EDU0042"

Common label prefixes ("RFP", "Tender No.", "NIT No", "Ref:", "Solicitation Number") are
dropped, then everything but letters and digits is removed and the rest upper-cased. A
prefix glued to the identifier without a separator ("RFP12345") is part of the id.
"""

from __future__ import annotations

import re

_PREFIX_WORDS = (
    "solicitation",
    "tender",
    "notice",
    "reference",
    "ref",
    "bid",
    "nit",
    "rfp",
    "rfq",
    "rfi",
    "eoi",
    "ifb",
    "number",
    "num",
    "no",
)
# One or more label words, each followed by a separator (space, punctuation) or end.
_PREFIX_RE = re.compile(
    r"^(?:(?:" + "|".join(_PREFIX_WORDS) + r")(?=[\s\.:#\-/_,]|$)[\s\.:#\-/_,]*)+",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


def normalized_reference(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    text = _PREFIX_RE.sub("", text)
    key = _NON_ALNUM_RE.sub("", text.upper())
    return key or None
