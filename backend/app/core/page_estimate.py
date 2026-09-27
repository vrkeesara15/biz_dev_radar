"""Deterministic page estimate for draft text (SPEC 8 agent 8: "page-limit overruns").

The red-team reviewer runs before anything is exported, so the overrun check cannot read
a rendered document: it counts words and divides by the words a page holds under the
solicitation's own font and margin rules. Pure -- no I/O, no model call -- so a page
limit is never something the model "decided".

    est = estimate_pages(body_text, font_size_pt=10, margins="0.75 inch")
    est.pages                       # 3.4
    over = overrun(body_text, limit=2, font_size_pt=10)
    over.over_by                    # 1.4 pages over the limit

Reference point: 500 words fill one single-spaced US-Letter page set in 12 pt with 1 inch
margins. Smaller type and narrower margins fit more words (text area scales with the
margins, character area with the square of the point size); the result is clamped so an
absurd rule cannot produce an absurd estimate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

BASE_WORDS_PER_PAGE = 500
REFERENCE_FONT_PT = 12.0
MIN_WORDS_PER_PAGE = 120
MAX_WORDS_PER_PAGE = 1500
MIN_FONT_PT = 6.0
MAX_FONT_PT = 24.0

# US Letter with 1 inch margins: the reference text area in square inches
PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0
REFERENCE_MARGIN_IN = 1.0
REFERENCE_AREA = (PAGE_WIDTH_IN - 2 * REFERENCE_MARGIN_IN) * (
    PAGE_HEIGHT_IN - 2 * REFERENCE_MARGIN_IN
)
MIN_MARGIN_IN = 0.25
MAX_MARGIN_IN = 2.5

_WORD_RE = re.compile(r"[^\s]+")
_MARGIN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(inch|inches|in\b|\"|cm|centimet(?:er|re)s?|mm)", re.IGNORECASE
)
_CM_PER_INCH = 2.54


def count_words(text: str) -> int:
    """Whitespace-separated tokens; a table cell or a bullet counts like a word."""
    return len(_WORD_RE.findall(text or ""))


def parse_margin_inches(margins: str | None) -> float | None:
    """The smallest margin the rule states, in inches (None when it states none)."""
    if not margins:
        return None
    found = _MARGIN_RE.findall(margins)
    if not found:
        return None
    values: list[float] = []
    for raw, unit in found:
        unit = unit.lower()
        value = float(raw)
        if unit.startswith("cm") or unit.startswith("centimet"):
            value /= _CM_PER_INCH
        elif unit == "mm":
            value /= _CM_PER_INCH * 10
        values.append(value)
    if not values:  # pragma: no cover - the regex only matches known units
        return None
    return max(MIN_MARGIN_IN, min(MAX_MARGIN_IN, min(values)))


def words_per_page(font_size_pt: float | None = None, margins: str | None = None) -> int:
    """How many words one page holds under these rules (clamped to a sane range)."""
    size = float(font_size_pt) if font_size_pt else REFERENCE_FONT_PT
    size = max(MIN_FONT_PT, min(MAX_FONT_PT, size))
    font_factor = (REFERENCE_FONT_PT / size) ** 2
    margin = parse_margin_inches(margins)
    if margin is None:
        area_factor = 1.0
    else:
        area = (PAGE_WIDTH_IN - 2 * margin) * (PAGE_HEIGHT_IN - 2 * margin)
        area_factor = area / REFERENCE_AREA
    words = round(BASE_WORDS_PER_PAGE * font_factor * area_factor)
    return max(MIN_WORDS_PER_PAGE, min(MAX_WORDS_PER_PAGE, words))


@dataclass(frozen=True, slots=True)
class PageEstimate:
    words: int = 0
    words_per_page: int = BASE_WORDS_PER_PAGE
    pages: float = 0.0
    limit: int | None = None

    @property
    def over(self) -> bool:
        return self.limit is not None and self.pages > self.limit

    @property
    def over_by(self) -> float:
        if self.limit is None:
            return 0.0
        return round(max(0.0, self.pages - self.limit), 1)

    @property
    def words_to_cut(self) -> int:
        """How many words must go for the text to fit the limit (0 when it fits)."""
        if self.limit is None or not self.over:
            return 0
        return max(0, self.words - self.limit * self.words_per_page)

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "words": self.words,
            "words_per_page": self.words_per_page,
            "pages": self.pages,
            "limit": self.limit,
            "over_by": self.over_by,
            "words_to_cut": self.words_to_cut,
        }


def estimate_pages(
    text: str,
    *,
    font_size_pt: float | None = None,
    margins: str | None = None,
    limit: int | None = None,
) -> PageEstimate:
    """Pages `text` would fill under the solicitation's font / margin rules."""
    per_page = words_per_page(font_size_pt, margins)
    words = count_words(text)
    pages = math.ceil(words / per_page * 10) / 10 if words else 0.0
    return PageEstimate(words=words, words_per_page=per_page, pages=pages, limit=limit)


def fits(estimate: PageEstimate) -> bool:
    return not estimate.over
