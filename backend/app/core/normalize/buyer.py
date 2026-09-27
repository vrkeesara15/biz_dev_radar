"""Buyer-name normaliser for dedupe (SPEC 5.4: same buyer across sources).

    normalized_buyer("Tata Consultancy Services Pvt. Ltd.") == "tata consultancy services"
    normalized_buyer("The Boeing Company") == "boeing"

Lower-case, punctuation removed, a leading article and trailing legal-form suffixes
dropped, whitespace collapsed. A name that consists only of a suffix word survives.
Non-Latin letters (Devanagari) are kept: an Indian buyer may be named in Hindi.

For `region="in"` (M3-06) a transliteration/abbreviation table runs first, so the same
body spelled by two portals lands on one key:

    normalized_buyer("Lucknow Nagar Nigam", region="in")
        == normalized_buyer("Lucknow Municipal Corporation", region="in")
        == "lucknow municipal"
    normalized_buyer("O/o the Chief Engineer, PWD", region="in")
        == normalized_buyer("Office of the Chief Engineer (PWD)", region="in")
        == "chief engineer pwd"
"""

from __future__ import annotations

import re
import unicodedata

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
_COMBINING = frozenset({"Mn", "Mc"})  # Indic matras: part of the word, not punctuation

# --- India (region "in") ------------------------------------------------------------
# Word-for-word transliterations and abbreviations seen on CPPP / GePNIC / GeM. The value
# is the canonical English word; an empty tuple in IN_PHRASES drops the phrase.
IN_WORDS: dict[str, str] = {
    "govt": "government",
    "goverment": "government",  # common portal typo
    "sarkar": "government",
    "dept": "department",
    "deptt": "department",
    "dpt": "department",
    "vibhag": "department",
    "vibhaag": "department",
    "nigam": "corporation",
    "corpn": "corporation",
    "corporations": "corporation",
    "ltd": "limited",
    "pvt": "private",
    "zilla": "district",
    "zila": "district",
    "jila": "district",
    "dist": "district",
    "distt": "district",
    "palika": "municipality",
    "nagarpalika": "municipality",
    "mahanagarpalika": "municipality",
    "engg": "engineering",
    "rajya": "state",
    "kendriya": "central",
    "bhavan": "building",
    "karyalaya": "office",
    "karyalay": "office",
}
# Multi-word forms, applied before IN_WORDS (longest first).
IN_PHRASES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("office", "of", "the"), ()),
    (("o", "o", "the"), ()),  # "O/o the Chief Engineer"
    (("office", "of"), ()),
    (("o", "o"), ()),
    (("nagar", "nigam"), ("municipal", "corporation")),
    (("nagar", "palika"), ("municipality",)),
    (("nagar", "panchayat"), ("town", "panchayat")),
    (("mahanagar", "palika"), ("municipal", "corporation")),
    (("municipal", "corporations"), ("municipal", "corporation")),
)
_AMPERSAND_RE = re.compile(r"\s*&\s*")


def _split_words(text: str) -> list[str]:
    r"""Split on separators, keeping letters and digits of any script.

    `[\W_]+` would drop Indic combining marks (Devanagari matras, Tamil vowel signs),
    which are part of the word: "मुख्य" must not become "म ख य".
    """
    words: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isalnum() or unicodedata.category(char) in _COMBINING:
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _apply_in_phrases(words: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(words):
        for source, target in IN_PHRASES:
            if tuple(words[i : i + len(source)]) == source:
                out.extend(target)
                i += len(source)
                break
        else:
            out.append(words[i])
            i += 1
    return out


def normalized_buyer(value: str | None, *, region: str | None = None) -> str | None:
    if not value:
        return None
    text = value.lower()
    if region == "in":
        # "Health & Family Welfare" and "Health and Family Welfare" are one department
        text = _AMPERSAND_RE.sub(" and ", text)
    # "L.L.C." / "U.S." -> "llc" / "us": dots join abbreviation letters, they never separate words
    words = _split_words(text.replace(".", ""))
    if not words:
        return None
    if len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    if region == "in":
        words = _apply_in_phrases(words)
        words = [IN_WORDS.get(w, w) for w in words]
        if not words:
            return None
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or None
