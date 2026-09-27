"""Pure helpers for the requirements extractor (SPEC 8 agent 2, 12): page-tagged batches,
citation validation (page must be in the batch, quote must be found on that page),
cross-batch de-duplication and stable R-### ids. No I/O, no LLM.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

RequirementType = Literal[
    "shall", "must", "should", "eligibility", "format", "submission", "evaluation"
]
REQUIREMENT_TYPES: tuple[str, ...] = (
    "shall",
    "must",
    "should",
    "eligibility",
    "format",
    "submission",
    "evaluation",
)

BATCH_CHARS = 12_000
PAGE_TAG = "[Page {n}]"
QUOTE_FUZZ_THRESHOLD = 92  # partial_ratio; tolerates OCR noise, rejects invented quotes
DEDUPE_THRESHOLD = 95  # token_set_ratio between two requirement texts
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True, slots=True)
class PageText:
    number: int
    text: str


@dataclass(frozen=True, slots=True)
class DocText:
    """One parsed document: id + display name + its pages (form-feed split, 1-based)."""

    document_id: str
    name: str
    pages: tuple[PageText, ...]

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


@dataclass(frozen=True, slots=True)
class Batch:
    index: int
    document_id: str
    name: str
    pages: tuple[PageText, ...]

    @property
    def page_numbers(self) -> frozenset[int]:
        return frozenset(p.number for p in self.pages)

    @property
    def first_page(self) -> int:
        return min(p.number for p in self.pages)

    @property
    def last_page(self) -> int:
        return max(p.number for p in self.pages)

    def page_text(self, number: int) -> str:
        return "\n".join(p.text for p in self.pages if p.number == number)

    def tagged_text(self) -> str:
        return "\n\n".join(f"{PAGE_TAG.format(n=p.number)}\n{p.text}" for p in self.pages)


def normalize(text: str) -> str:
    """Lowercase, punctuation and whitespace folded: what quote matching compares."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def build_batches(documents: list[DocText], *, max_chars: int = BATCH_CHARS) -> list[Batch]:
    """Consecutive pages of one document per batch, up to `max_chars` of text. A page
    larger than the budget is split into parts that keep its page number, so every
    batch still knows exactly which pages it holds."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    batches: list[Batch] = []
    for doc in documents:
        current: list[PageText] = []
        size = 0
        for page in doc.pages:
            if not page.text.strip():
                continue
            parts = _split_page(page, max_chars)
            for part in parts:
                if current and size + len(part.text) > max_chars:
                    batches.append(Batch(len(batches), doc.document_id, doc.name, tuple(current)))
                    current, size = [], 0
                current.append(part)
                size += len(part.text)
        if current:
            batches.append(Batch(len(batches), doc.document_id, doc.name, tuple(current)))
    return batches


def _split_page(page: PageText, max_chars: int) -> list[PageText]:
    if len(page.text) <= max_chars:
        return [page]
    parts: list[PageText] = []
    text = page.text
    while text:
        cut = max_chars
        if len(text) > max_chars:
            boundary = text.rfind("\n", max_chars // 2, max_chars)
            if boundary > 0:
                cut = boundary + 1
        parts.append(PageText(page.number, text[:cut]))
        text = text[cut:]
    return parts


def estimate_batches(total_chars: int, *, max_chars: int = BATCH_CHARS) -> int:
    return max(1, math.ceil(max(total_chars, 0) / max_chars)) if total_chars > 0 else 0


@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    page: int
    type: str
    quote: str
    volume: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: str
    text: str
    page: int


@dataclass(frozen=True, slots=True)
class Cited:
    """A candidate that passed validation, bound to its document."""

    document_id: str
    document_name: str
    page: int
    text: str
    type: str
    quote: str
    volume: str | None
    confidence: float


def quote_found(quote: str, page_text: str) -> bool:
    """Normalized substring match, else a high partial-ratio (OCR noise, hyphenation)."""
    needle, hay = normalize(quote), normalize(page_text)
    if not needle or not hay:
        return False
    if needle in hay:
        return True
    if len(needle) < 12:  # too short for fuzzy matching to mean anything
        return False
    return fuzz.partial_ratio(needle, hay) >= QUOTE_FUZZ_THRESHOLD


def validate_candidates(
    batch: Batch, candidates: list[Candidate]
) -> tuple[list[Cited], list[Rejection]]:
    """Keep only candidates whose page is in the batch and whose quote is on that page."""
    accepted: list[Cited] = []
    rejected: list[Rejection] = []
    for cand in candidates:
        if cand.type not in REQUIREMENT_TYPES:
            rejected.append(Rejection(f"unknown type {cand.type!r}", cand.text, cand.page))
            continue
        if cand.page not in batch.page_numbers:
            rejected.append(
                Rejection(
                    f"page {cand.page} is not in this batch "
                    f"({batch.first_page}-{batch.last_page} of {batch.name})",
                    cand.text,
                    cand.page,
                )
            )
            continue
        if not quote_found(cand.quote, batch.page_text(cand.page)):
            rejected.append(Rejection("quote not found on the cited page", cand.text, cand.page))
            continue
        if not cand.text.strip():
            rejected.append(Rejection("empty requirement text", cand.text, cand.page))
            continue
        volume = " ".join(cand.volume.split()) if cand.volume and cand.volume.strip() else None
        accepted.append(
            Cited(
                document_id=batch.document_id,
                document_name=batch.name,
                page=cand.page,
                text=" ".join(cand.text.split()),
                type=cand.type,
                quote=" ".join(cand.quote.split()),
                volume=volume,
                confidence=min(max(cand.confidence, 0.0), 1.0),
            )
        )
    return accepted, rejected


def is_duplicate(text: str, others: list[str], *, threshold: int = DEDUPE_THRESHOLD) -> bool:
    needle = normalize(text)
    return any(fuzz.token_set_ratio(needle, normalize(o)) >= threshold for o in others)


def dedupe(cited: list[Cited], *, threshold: int = DEDUPE_THRESHOLD) -> list[Cited]:
    """Drop near-identical texts (a clause repeated in a summary and the body keeps the
    first occurrence in document order)."""
    kept: list[Cited] = []
    texts: list[str] = []
    for item in cited:
        if is_duplicate(item.text, texts, threshold=threshold):
            continue
        kept.append(item)
        texts.append(item.text)
    return kept


def req_id(index: int) -> str:
    """R-001, R-002, ... (1-based)."""
    if index < 1:
        raise ValueError("requirement index is 1-based")
    return f"R-{index:03d}"


@dataclass(slots=True)
class MergeResult:
    requirements: list[Cited] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)


def merge(batches_accepted: list[list[Cited]]) -> MergeResult:
    """Concatenate batch results in document order, de-duplicate and number them."""
    flat = [item for accepted in batches_accepted for item in accepted]
    unique = dedupe(flat)
    return MergeResult(requirements=unique, ids=[req_id(i) for i in range(1, len(unique) + 1)])
