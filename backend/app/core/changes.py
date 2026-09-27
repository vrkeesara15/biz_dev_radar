"""Change detection (SPEC 5.4): canonical payload, content_hash, field diff, change kinds.

content_hash = sha256 over a canonical JSON of the normalised fields plus the sorted
document hashes (a document's own sha256 when known, else the sha256 of its URL). Volatile
bookkeeping (raw_ref, detail_status, parent linkage) is excluded so a re-fetch of an
unchanged notice hashes identically.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.core.opportunity import DocumentKind, DocumentRef, OpportunityIn

HASHED_FIELDS: tuple[str, ...] = (
    "source_id",
    "external_id",
    "source_url",
    "region",
    "country",
    "currency",
    "notice_type",
    "title",
    "description_text",
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
    "source_tz",
    "contacts",
    "eligibility",
    "status",
    "extra",
)
# also_from is dedupe bookkeeping written by the pipeline (M2-10), never by an adapter.
VOLATILE_EXTRA_KEYS = frozenset({"raw_ref", "also_from"})
DEADLINE_FIELDS = ("response_due_at", "questions_due_at", "opening_at", "prebid_meeting_at")


class ChangeKind(StrEnum):
    DEADLINE_MOVED = "deadline_moved"
    NEW_ATTACHMENT = "new_attachment"
    QA_POSTED = "qa_posted"
    CANCELLED = "cancelled"
    AWARDED = "awarded"
    DESCRIPTION_UPDATED = "description_updated"
    OTHER = "other"


_QA_RE = re.compile(r"\b(q\s*&\s*a|q\s*and\s*a|questions?\s*(and|&)\s*answers?|faq)\b", re.I)


def _plain(value: Any) -> Any:
    """JSON-safe, order-stable representation."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list | tuple | set | frozenset):
        items = [_plain(v) for v in value]
        return sorted(items, key=json.dumps) if isinstance(value, set | frozenset) else items
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="python"))
    return str(value)


def document_hash(doc: DocumentRef | Mapping[str, Any]) -> str:
    """The document's sha256 when the source (or parser) supplied one, else sha256(url)."""
    if isinstance(doc, Mapping):
        sha, url = doc.get("sha256") or doc.get("hash"), str(doc.get("url") or "")
    else:
        sha, url = doc.sha256, doc.url
    return str(sha) if sha else hashlib.sha256(url.encode("utf-8")).hexdigest()


def document_entries(docs: Iterable[DocumentRef | Mapping[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for doc in docs:
        if isinstance(doc, Mapping):
            url, name, kind = doc.get("url"), doc.get("file_name"), doc.get("kind")
        else:
            url, name, kind = doc.url, doc.file_name, doc.kind
        entries.append(
            {
                "url": str(url or ""),
                "file_name": name,
                "kind": kind.value if isinstance(kind, StrEnum) else kind,
                "hash": document_hash(doc),
            }
        )
    return sorted(entries, key=lambda e: e["url"])


def canonical_payload(values: OpportunityIn | Mapping[str, Any]) -> dict[str, Any]:
    """Normalised fields + document hashes, JSON-safe and order-stable."""
    source: Mapping[str, Any]
    source = values.model_dump(mode="python") if isinstance(values, OpportunityIn) else values
    payload: dict[str, Any] = {}
    for field in HASHED_FIELDS:
        value = source.get(field)
        if field == "extra" and isinstance(value, Mapping):
            value = {k: v for k, v in value.items() if k not in VOLATILE_EXTRA_KEYS}
        payload[field] = _plain(value)
    payload["documents"] = document_entries(source.get("documents") or [])
    return payload


def content_hash(values: OpportunityIn | Mapping[str, Any]) -> str:
    body = json.dumps(canonical_payload(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def diff_payloads(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Field-level diff {field: {"old": ..., "new": ...}} over the union of keys."""
    diff: dict[str, dict[str, Any]] = {}
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before != after:
            diff[key] = {"old": before, "new": after}
    return diff


def _added_documents(diff: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    change = diff.get("documents")
    if not change:
        return []
    old_urls = {d["url"] for d in change.get("old") or []}
    return [d for d in change.get("new") or [] if d["url"] not in old_urls]


def _looks_like_qa(doc: Mapping[str, Any]) -> bool:
    if doc.get("kind") == DocumentKind.QA.value:
        return True
    text = f"{doc.get('file_name') or ''} {doc.get('url') or ''}"
    return bool(_QA_RE.search(text))


def classify_changes(diff: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Change kinds for a diff (SPEC 5.4: deadline moved, new attachment, Q&A, cancelled)."""
    kinds: list[str] = []
    if any(field in diff for field in DEADLINE_FIELDS):
        kinds.append(ChangeKind.DEADLINE_MOVED)
    added = _added_documents(diff)
    if added:
        kinds.append(ChangeKind.NEW_ATTACHMENT)
        if any(_looks_like_qa(doc) for doc in added):
            kinds.append(ChangeKind.QA_POSTED)
    status = diff.get("status")
    if status:
        if status.get("new") == "cancelled":
            kinds.append(ChangeKind.CANCELLED)
        elif status.get("new") == "awarded":
            kinds.append(ChangeKind.AWARDED)
    if "description_text" in diff:
        kinds.append(ChangeKind.DESCRIPTION_UPDATED)
    if diff and not kinds:
        kinds.append(ChangeKind.OTHER)
    return [k.value if isinstance(k, ChangeKind) else k for k in kinds]
