"""Grounding validator (SPEC 8: "every factual claim about the company must cite a
profile record, past-performance ID or uploaded file; unsupported claims are highlighted
red and listed"). Pure: no I/O, no model calls.

    report = validate(body_text, citations, resolvable_tokens, profile_facts)
    report.flags            # [Flag(sentence, reason, detail)] -> rendered red in the UI
    report.unsupported_count

A sentence is SUPPORTED when it carries a citation token that resolves to one of the
tenant's own records. A sentence with no such token is checked for the things a proposal
must never assert without evidence -- numbers, organisation names, certifications, past
performance and marketing superlatives -- and each hit becomes a flag. A sentence that
already says [NEEDS INPUT: ...] is a placeholder: it is neither supported nor flagged,
because the drafter has already handed it to a human.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.citations import find_tokens, strip_tokens

REASON_NUMBERS = "numbers"
REASON_NAME = "name"
REASON_CERTIFICATION = "certification"
REASON_PAST_PERFORMANCE = "past_performance"
REASON_UNSUPPORTED_CLAIM = "unsupported_claim"
REASONS: tuple[str, ...] = (
    REASON_NUMBERS,
    REASON_NAME,
    REASON_CERTIFICATION,
    REASON_PAST_PERFORMANCE,
    REASON_UNSUPPORTED_CLAIM,
)

NEEDS_INPUT_MARKER = "[NEEDS INPUT"

# numbers that assert something: counts, percentages, money, durations
_NUMBER_RE = re.compile(
    r"(?<![\w-])(?:[$₹€£]\s?\d|\d[\d,.]*\s?(?:%|percent|per cent|million|billion|crore|lakh|"
    r"years?|months?|weeks?|days?|hours?|staff|people|employees|engineers|customers|"
    r"agencies|contracts?|workloads?|users?|sites?|k\b|m\b)|\d[\d,]{2,})",
    re.IGNORECASE,
)
# a bare year or a section reference is not a claim about the company
_HARMLESS_NUMBER_RE = re.compile(r"^(?:19|20)\d{2}$|^\d{1,2}$")

CERTIFICATION_TERMS: tuple[str, ...] = (
    "iso 9001",
    "iso 27001",
    "iso 20000",
    "iso 14001",
    "cmmi",
    "cmmc",
    "soc 2",
    "soc2",
    "fedramp",
    "fisma",
    "nist 800-171",
    "nist 800-53",
    "itar",
    "hipaa",
    "pci dss",
    "six sigma",
    "pmp",
    "8(a)",
    "hubzone",
    "wosb",
    "sdvosb",
    "gdpr",
    "stqc",
    "cert-in",
)

PAST_PERFORMANCE_PHRASES: tuple[str, ...] = (
    "past performance",
    "we delivered",
    "we have delivered",
    "we completed",
    "we have completed",
    "we migrated",
    "we implemented",
    "we supported",
    "we managed",
    "we built",
    "we operated",
    "our experience",
    "our team has",
    "we have served",
    "prior contract",
    "previous contract",
    "reference contract",
    "our clients include",
    "we currently support",
)

CLAIM_PHRASES: tuple[str, ...] = (
    "industry leading",
    "industry-leading",
    "world class",
    "world-class",
    "best in class",
    "best-in-class",
    "market leader",
    "the leading",
    "unmatched",
    "unrivalled",
    "unrivaled",
    "guarantee",
    "guaranteed",
    "zero defects",
    "100% success",
    "always on time",
    "the only vendor",
    "proven track record",
    "award-winning",
    "award winning",
)

# words that start a sentence and look like a proper noun without being one
_SENTENCE_STARTERS = frozenset(
    {
        "the",
        "our",
        "we",
        "this",
        "these",
        "those",
        "all",
        "each",
        "every",
        "for",
        "in",
        "on",
        "at",
        "as",
        "by",
        "with",
        "when",
        "where",
        "if",
        "during",
        "after",
        "before",
        "under",
        "over",
        "no",
        "not",
        "a",
        "an",
        "and",
        "but",
        "to",
        "from",
        "per",
        "using",
        "their",
        "its",
        "his",
        "her",
    }
)
_ORG_SUFFIXES = (
    "inc",
    "llc",
    "ltd",
    "limited",
    "corp",
    "corporation",
    "plc",
    "gmbh",
    "agency",
    "department",
    "administration",
    "bureau",
    "ministry",
    "university",
    "hospital",
    "bank",
    "systems",
    "technologies",
    "solutions",
    "services",
    "pvt",
)
_PROPER_NOUN_RE = re.compile(r"\b([A-Z][\w&.'-]*(?:\s+(?:of|and|for|the)?\s*[A-Z][\w&.'-]*)+)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[\s\n]+")


@dataclass(frozen=True, slots=True)
class Flag:
    sentence: str
    reason: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"sentence": self.sentence, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class GroundingReport:
    flags: list[Flag] = field(default_factory=list)
    supported_count: int = 0
    unsupported_count: int = 0
    placeholder_count: int = 0
    sentences: int = 0
    unresolved_tokens: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.flags

    def as_dict(self) -> dict[str, Any]:
        return {
            "flags": [f.as_dict() for f in self.flags],
            "supported_count": self.supported_count,
            "unsupported_count": self.unsupported_count,
            "placeholder_count": self.placeholder_count,
            "sentences": self.sentences,
            "unresolved_tokens": list(self.unresolved_tokens),
        }


MAX_HEADING_WORDS = 8


def split_lines(text: str) -> list[tuple[str, bool]]:
    """(line, is_bullet) for every non-empty line, bullet markers removed."""
    out: list[tuple[str, bool]] = []
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        bullet = stripped[:1] in "-*\u2022"
        body = stripped.lstrip("-*\u2022 ").strip()
        if body:
            out.append((body, bullet))
    return out


def split_sentences(text: str) -> list[str]:
    """Sentences of a plain-text body; blank lines and list bullets split too."""
    out: list[str] = []
    for line, _bullet in split_lines(text):
        out.extend(part.strip() for part in _SENTENCE_SPLIT_RE.split(line) if part.strip())
    return out


def is_heading(sentence: str, bullet: bool = False) -> bool:
    """A short line with no sentence-ending punctuation is a heading, not a claim
    ("Technical Approach"). A bullet is always a claim, however short."""
    return (
        not bullet
        and not sentence.rstrip().endswith((".", "!", "?"))
        and len(sentence.split()) <= MAX_HEADING_WORDS
    )


def _norm(value: str) -> str:
    return " ".join((value or "").lower().split())


def _known_facts(profile_facts: Iterable[str]) -> set[str]:
    return {_norm(fact) for fact in profile_facts if _norm(fact)}


def _numbers(sentence: str) -> str | None:
    for match in _NUMBER_RE.finditer(sentence):
        value = match.group(0).strip()
        if _HARMLESS_NUMBER_RE.match(value):
            continue
        return value
    return None


def _terms(sentence: str, terms: Sequence[str]) -> str | None:
    lowered = _norm(sentence)
    for term in terms:
        if term in lowered:
            return term
    return None


def _proper_nouns(sentence: str, known: set[str]) -> str | None:
    for match in _PROPER_NOUN_RE.finditer(sentence):
        phrase = match.group(1).strip(" .,")
        normalised = _norm(phrase)
        if not normalised or normalised in known:
            continue
        if any(normalised in fact or fact in normalised for fact in known):
            continue
        words = normalised.split()
        if words and words[0] in _SENTENCE_STARTERS:
            words = words[1:]
            phrase = " ".join(phrase.split()[1:])
        # a single word is only an organisation when it carries an org suffix
        # (an empty list fails this check too, so nothing below can see one)
        if len(words) < 2 and not any(w in _ORG_SUFFIXES for w in words):
            continue
        return phrase
    return None


def cited_tokens(text: str, resolvable: Collection[str]) -> tuple[list[str], list[str]]:
    """(tokens in `text` that resolve, tokens that do not)."""
    resolved: list[str] = []
    unresolved: list[str] = []
    for token in find_tokens(text):
        (resolved if token in resolvable else unresolved).append(token)
    return resolved, unresolved


def validate(
    body_text: str,
    citations: Sequence[dict[str, Any]] = (),
    resolvable_tokens: Collection[str] = (),
    profile_facts: Iterable[str] = (),
) -> GroundingReport:
    """Flag every sentence that asserts a company fact without a resolvable citation.

    `citations` is the version's stored citation list: its tokens count as resolvable
    even when the caller passed a narrower `resolvable_tokens` set (they were checked
    when the version was written). `profile_facts` are strings the tenant's own records
    contain -- the legal name, customers, certifications -- so the company's own name
    does not read as an unsupported third-party reference.
    """
    resolvable = set(resolvable_tokens) | {str(c.get("token")) for c in citations if c.get("token")}
    known = _known_facts(profile_facts)
    flags: list[Flag] = []
    supported = unsupported = placeholders = 0
    all_unresolved: list[str] = []

    sentences: list[str] = []
    for line, bullet in split_lines(body_text):
        parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(line) if p.strip()]
        if len(parts) == 1 and is_heading(parts[0], bullet):
            continue  # a heading is a label, not an assertion
        sentences.extend(parts)
    for sentence in sentences:
        resolved, unresolved = cited_tokens(sentence, resolvable)
        all_unresolved.extend(unresolved)
        bare = strip_tokens(sentence)
        if NEEDS_INPUT_MARKER in sentence:
            placeholders += 1
            continue
        if resolved:
            supported += 1
            continue
        reasons: list[tuple[str, str]] = []
        number = _numbers(bare)
        if number:
            reasons.append((REASON_NUMBERS, number))
        certification = _terms(bare, CERTIFICATION_TERMS)
        if certification:
            reasons.append((REASON_CERTIFICATION, certification))
        performance = _terms(bare, PAST_PERFORMANCE_PHRASES)
        if performance:
            reasons.append((REASON_PAST_PERFORMANCE, performance))
        claim = _terms(bare, CLAIM_PHRASES)
        if claim:
            reasons.append((REASON_UNSUPPORTED_CLAIM, claim))
        name = _proper_nouns(bare, known)
        if name:
            reasons.append((REASON_NAME, name))
        if reasons:
            unsupported += 1
            flags.extend(
                Flag(sentence=bare, reason=reason, detail=detail) for reason, detail in reasons
            )
    return GroundingReport(
        flags=flags,
        supported_count=supported,
        unsupported_count=unsupported,
        placeholder_count=placeholders,
        sentences=len(sentences),  # headings are not sentences
        unresolved_tokens=tuple(dict.fromkeys(all_unresolved)),
    )
