"""Saved-search filter sets and whether one notice satisfies them (M4-08). Pure.

    filters = SearchFilters.from_dict({"region": "us", "naics": ["541511"], "min_score": 60})
    filters.matches(match_opportunity_from_row(row), now=now, score=Decimal(72))

The vocabulary is exactly the `GET /api/v1/opportunities` query string (SPEC 10.3), so a
user can save whatever they searched: `q`, `region`, `type`, `naics`, `status`,
`due_before`, `due_within_days`, `buyer` and `min_score`. Everything is optional and an
absent filter never narrows anything.

`q` is applied here as a case-insensitive whole-word AND over title + summary rather than
as Postgres full text: an alert rule is evaluated for ONE notice that is already in hand,
and re-running websearch_to_tsquery per notice per rule would be a query per rule.
Quoted phrases and a leading `-` (exclude) are honoured, which covers what the search box
produces; stemming is not.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.matching.types import MatchOpportunity
from app.core.normalize.buyer import normalized_buyer

MAX_TERMS = 20
_PHRASE_RE = re.compile(r'(-?)"([^"]+)"|(-?)(\S+)')
_WORD_RE = re.compile(r"[^a-z0-9]+")


def _tuple(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], ()):
        return ()
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, Sequence):
        parts = [str(v) for v in value]
    else:
        parts = [str(value)]
    return tuple(p.strip() for p in parts if str(p).strip())


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    number = int(value)
    return number


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass(frozen=True, slots=True)
class SearchFilters:
    q: str | None = None
    region: str | None = None
    notice_types: tuple[str, ...] = ()
    naics: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    buyers: tuple[str, ...] = ()
    due_before: datetime | None = None
    due_within_days: int | None = None
    min_score: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SearchFilters:
        """Tolerant loader: the stored jsonb, or the raw query string the UI sent."""
        raw = dict(data or {})
        return cls(
            q=(str(raw["q"]).strip() or None) if raw.get("q") else None,
            region=(str(raw["region"]).strip().lower() or None) if raw.get("region") else None,
            notice_types=tuple(
                t.lower() for t in _tuple(raw.get("type") or raw.get("notice_types"))
            ),
            naics=tuple(c.upper() for c in _tuple(raw.get("naics"))),
            statuses=tuple(s.lower() for s in _tuple(raw.get("status") or raw.get("statuses"))),
            buyers=_tuple(raw.get("buyer") or raw.get("buyers")),
            due_before=_dt(raw.get("due_before")),
            due_within_days=_int(raw.get("due_within_days")),
            min_score=_int(raw.get("min_score")),
        )

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.q:
            out["q"] = self.q
        if self.region:
            out["region"] = self.region
        if self.notice_types:
            out["type"] = list(self.notice_types)
        if self.naics:
            out["naics"] = list(self.naics)
        if self.statuses:
            out["status"] = list(self.statuses)
        if self.buyers:
            out["buyer"] = list(self.buyers)
        if self.due_before is not None:
            out["due_before"] = self.due_before.isoformat()
        if self.due_within_days is not None:
            out["due_within_days"] = self.due_within_days
        if self.min_score is not None:
            out["min_score"] = self.min_score
        return out

    @property
    def is_empty(self) -> bool:
        return self.as_dict() == {}

    def matches(
        self,
        opp: MatchOpportunity,
        *,
        now: datetime,
        score: Decimal | int | float | None = None,
    ) -> bool:
        """True when the notice (and its score) satisfies every filter that is set."""
        if self.region and opp.region != self.region:
            return False
        if self.notice_types and opp.notice_type.lower() not in self.notice_types:
            return False
        if self.statuses and opp.status.lower() not in self.statuses:
            return False
        if self.naics and not ({c.upper() for c in opp.naics} & set(self.naics)):
            return False
        if self.buyers and not _buyer_hit(self.buyers, opp):
            return False
        if self.due_before is not None and (
            opp.response_due_at is None or opp.response_due_at > self.due_before
        ):
            return False
        if self.due_within_days is not None:
            horizon = now + timedelta(days=self.due_within_days)
            if opp.response_due_at is None or opp.response_due_at > horizon:
                return False
        if self.min_score is not None and (
            score is None or Decimal(str(score)) < Decimal(self.min_score)
        ):
            return False
        return self.q is None or text_matches(self.q, opp.text)


def _buyer_hit(buyers: tuple[str, ...], opp: MatchOpportunity) -> bool:
    wanted = [normalized_buyer(b) for b in buyers]
    known = [normalized_buyer(b) for b in opp.buyers]
    return any(w and b and (w == b or f" {w} " in f" {b} ") for w in wanted for b in known)


def parse_query(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(required, excluded) terms of a search box string; "quoted phrases" stay together."""
    required: list[str] = []
    excluded: list[str] = []
    for match in _PHRASE_RE.finditer(query.lower()):
        negated = bool(match.group(1) or match.group(3))
        term = (match.group(2) or match.group(4) or "").strip()
        if not term or term in {"or", "and"}:
            continue
        (excluded if negated else required).append(term)
    return tuple(required[:MAX_TERMS]), tuple(excluded[:MAX_TERMS])


def _words(text: str) -> str:
    """Lower-cased text with punctuation flattened to spaces, padded for whole-word tests."""
    return f" {' '.join(_WORD_RE.sub(' ', text.lower()).split())} "


def text_matches(query: str, text: str) -> bool:
    """Every required term present and no excluded term present, whole-word."""
    required, excluded = parse_query(query)
    haystack = _words(text)
    if any(f" {_words(term).strip()} " in haystack for term in excluded):
        return False
    return all(f" {_words(term).strip()} " in haystack for term in required)


__all__ = ["SearchFilters", "parse_query", "text_matches"]
