"""Dedupe rules (SPEC 5.4), pure: richness of a record and the winner choice.

Richness = number of non-empty canonical fields + number of documents. On a merge the
richer record survives; the other gets `duplicate_of` and its source link is kept on
the survivor (`extra.also_from`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Canonical SPEC 5.3 content fields that make a record "richer". Bookkeeping columns
# (raw_ref, content_hash, version, extra, source ids) never count.
RICHNESS_FIELDS: tuple[str, ...] = (
    "source_url",
    "title",
    "description_text",
    "summary_ai",
    "solicitation_number",
    "buyer_org",
    "buyer_sub_org",
    "buyer_office",
    "buyer_hierarchy",
    "naics",
    "psc",
    "aln",
    "india_category",
    "set_aside",
    "reservation",
    "place_of_performance",
    "estimated_value_min",
    "estimated_value_max",
    "emd_amount",
    "tender_fee",
    "posted_at",
    "questions_due_at",
    "prebid_meeting_at",
    "response_due_at",
    "opening_at",
    "archive_at",
    "contacts",
    "eligibility",
    "incumbent",
    "prior_award_value",
    "prior_pop_end",
)

CROSS_SOURCE_KEY = "cross_source_key"
FUZZY_TITLE = "fuzzy_title"
# pg_trgm similarity(title, title) threshold and the response-due window (SPEC 5.4)
TITLE_SIMILARITY = 0.9
DUE_WINDOW_SECONDS = 86_400


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return len(value) > 0
    return True


def richness(values: Mapping[str, Any], *, document_count: int) -> int:
    fields = sum(1 for name in RICHNESS_FIELDS if _present(values.get(name)))
    return fields + max(document_count, 0)


@dataclass(frozen=True, slots=True)
class Candidate:
    id: Any
    richness: int
    created_at: datetime | None = None


def pick_winner(candidates: Sequence[Candidate]) -> Candidate:
    """Richest wins; ties go to the older record; a full tie keeps the first given."""
    if not candidates:
        raise ValueError("pick_winner needs at least one candidate")
    best = candidates[0]
    for cand in candidates[1:]:
        if cand.richness > best.richness or (
            cand.richness == best.richness
            and cand.created_at is not None
            and best.created_at is not None
            and cand.created_at < best.created_at
        ):
            best = cand
    return best


def also_from_entries(
    existing: Sequence[Mapping[str, Any]] | None, new_entries: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Append source links to `also_from`, deduplicated by (source_id, external_id)."""
    out: list[dict[str, Any]] = [dict(e) for e in (existing or [])]
    seen = {(e.get("source_id"), e.get("external_id")) for e in out}
    for entry in new_entries:
        key = (entry.get("source_id"), entry.get("external_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(entry))
    return out
