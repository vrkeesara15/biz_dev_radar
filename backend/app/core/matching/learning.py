"""SPEC 6 learning loop (M4-07): turn thumbs into keyword suggestions. Pure.

    observations = [Observation(terms=candidate_terms(title), positive=True), ...]
    for lift in keyword_lift(observations):
        lift.term, lift.kind, lift.delta_weight, lift.evidence()

The measure is LIFT: how much more (or less) often a match was thumbed up when a term
was present than the profile's own baseline.

    baseline = ups / total                     over every piece of feedback in the window
    rate     = ups_with_term / with_term       over the feedback whose notice had the term
    lift     = rate - baseline                 in [-1, 1]

A term only becomes a suggestion when it has enough support (MIN_SUPPORT observations
with it AND at least one without, so the baseline means something) and the lift clears
MIN_LIFT. A positive lift suggests an INCLUDE keyword, a negative one an EXCLUDE keyword.
`delta_weight` is `lift * DELTA_SCALE` rounded to one decimal and clamped to
+/- MAX_DELTA, so it lands on the same 1-decimal scale as `profile_keywords.weight`.

Nothing here is ever applied automatically (SPEC 6: "never silently applied"); the owner
approves a suggestion through the API.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

MIN_SUPPORT = 3
MIN_LIFT = Decimal("0.15")
DELTA_SCALE = Decimal(2)
MAX_DELTA = Decimal("2.0")
MAX_TERMS_PER_NOTICE = 60
MIN_TERM_CHARS = 4
MAX_TERM_CHARS = 60

INCLUDE = "include"
EXCLUDE = "exclude"

_TOKEN_RE = re.compile(r"[a-z][a-z0-9'\-]+")
_TENTH = Decimal("0.1")
_HUNDREDTH = Decimal("0.01")

# Small, deliberately boring stop list: procurement boilerplate plus English function
# words. Anything that survives it still has to clear MIN_SUPPORT and MIN_LIFT.
STOPWORDS: frozenset[str] = frozenset(
    [
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "also",
        "amendment",
        "and",
        "any",
        "are",
        "attachment",
        "award",
        "awarded",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "can",
        "cannot",
        "contract",
        "contractor",
        "could",
        "dated",
        "department",
        "does",
        "doing",
        "done",
        "down",
        "during",
        "each",
        "either",
        "federal",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "however",
        "into",
        "its",
        "itself",
        "just",
        "more",
        "most",
        "must",
        "nba",
        "need",
        "needed",
        "nor",
        "not",
        "notice",
        "number",
        "office",
        "only",
        "other",
        "otherwise",
        "our",
        "ours",
        "out",
        "over",
        "own",
        "performance",
        "please",
        "provide",
        "provided",
        "request",
        "requirement",
        "requirements",
        "response",
        "responses",
        "same",
        "services",
        "shall",
        "she",
        "should",
        "since",
        "solicitation",
        "some",
        "state",
        "such",
        "support",
        "sure",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "thus",
        "tender",
        "their",
        "under",
        "until",
        "upon",
        "very",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "within",
        "without",
        "work",
        "would",
        "you",
        "your",
        "yours",
    ]
)


@dataclass(frozen=True, slots=True)
class Observation:
    """One piece of feedback: the terms its notice contained and the thumb."""

    terms: frozenset[str]
    positive: bool


@dataclass(frozen=True, slots=True)
class TermLift:
    term: str
    kind: str  # include | exclude
    support: int  # observations whose notice contained the term
    positives: int
    rate: Decimal  # P(up | term present)
    baseline: Decimal  # P(up) over the whole window
    lift: Decimal  # rate - baseline
    delta_weight: Decimal

    def evidence(self) -> dict[str, Any]:
        """The jsonb the owner sees next to the suggestion."""
        return {
            "support": self.support,
            "positives": self.positives,
            "negatives": self.support - self.positives,
            "rate": float(self.rate),
            "baseline": float(self.baseline),
            "lift": float(self.lift),
        }


def candidate_terms(
    *texts: str | None,
    max_terms: int = MAX_TERMS_PER_NOTICE,
    extra: Iterable[str] = (),
) -> frozenset[str]:
    """Unigrams and adjacent bigrams of the notice text, minus stop words and numbers.

    `extra` carries terms the owner already watches, so an existing keyword is re-weighed
    even when it never survives the tokeniser (a phrase of three words, say).
    """
    words: list[str] = []
    for text in texts:
        for token in _TOKEN_RE.findall((text or "").lower()):
            if len(token) < MIN_TERM_CHARS or token in STOPWORDS:
                words.append("")  # a gap: never bridge a bigram across a dropped word
                continue
            words.append(token)
        words.append("")
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        if term and term not in seen and len(term) <= MAX_TERM_CHARS:
            seen.add(term)
            out.append(term)

    for index, word in enumerate(words):
        if not word:
            continue
        add(word)
        nxt = words[index + 1] if index + 1 < len(words) else ""
        if nxt:
            add(f"{word} {nxt}")
    for term in extra:
        cleaned = " ".join(str(term).lower().split())
        if cleaned:
            add(cleaned)
    return frozenset(out[:max_terms])


def _rate(positives: int, total: int) -> Decimal:
    """`total` is never 0: keyword_lift returns early below min_support, and a term is
    only counted on observations that contain it."""
    return (Decimal(positives) / Decimal(total)).quantize(_HUNDREDTH, rounding=ROUND_HALF_UP)


def delta_for(lift: Decimal) -> Decimal:
    scaled = (lift * DELTA_SCALE).quantize(_TENTH, rounding=ROUND_HALF_UP)
    return max(-MAX_DELTA, min(MAX_DELTA, scaled))


def keyword_lift(
    observations: Sequence[Observation],
    *,
    min_support: int = MIN_SUPPORT,
    min_lift: Decimal = MIN_LIFT,
) -> list[TermLift]:
    """Every term whose presence moves the thumb rate far enough to be worth asking about.

    Ordered by |lift| descending, then by term, so the owner sees the strongest first.
    """
    total = len(observations)
    if total < min_support:
        return []
    baseline = _rate(sum(1 for o in observations if o.positive), total)
    counts: dict[str, list[int]] = {}
    for observation in observations:
        for term in observation.terms:
            entry = counts.setdefault(term, [0, 0])
            entry[0] += 1
            if observation.positive:
                entry[1] += 1
    out: list[TermLift] = []
    for term, (support, positives) in counts.items():
        if support < min_support or support == total:
            # a term in EVERY notice explains nothing: its rate is the baseline
            continue
        rate = _rate(positives, support)
        lift = rate - baseline
        if abs(lift) < min_lift:
            continue
        out.append(
            TermLift(
                term=term,
                kind=INCLUDE if lift > 0 else EXCLUDE,
                support=support,
                positives=positives,
                rate=rate,
                baseline=baseline,
                lift=lift,
                delta_weight=delta_for(lift),
            )
        )
    out.sort(key=lambda t: (-abs(t.lift), t.term))
    return out


__all__ = [
    "DELTA_SCALE",
    "EXCLUDE",
    "INCLUDE",
    "MAX_DELTA",
    "MIN_LIFT",
    "MIN_SUPPORT",
    "STOPWORDS",
    "Observation",
    "TermLift",
    "candidate_terms",
    "delta_for",
    "keyword_lift",
]
