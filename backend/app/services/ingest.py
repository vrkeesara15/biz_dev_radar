"""Normalisation pipeline (SPEC 5, 5.4): upsert by (source_id, external_id), content_hash,
versions with field-level diffs, amendment linking and events.

    result = await ingest(session, opportunity_in, raw_ref=raw.raw_ref)

- unchanged (same content_hash): last_seen_at/raw_ref refreshed, no version, no event
- new: opportunities row at version 1, `opportunity.created`
- changed: version += 1, opportunity_versions row {diff, changes, content_hash},
  `opportunity.amended` for matching and notifications
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.base import OpportunityIn, RawRecord
from app.core.changes import canonical_payload, classify_changes, content_hash, diff_payloads
from app.core.config import Settings, get_settings
from app.core.money import to_usd
from app.core.normalize.buyer import normalized_buyer
from app.core.normalize.reference import normalized_reference
from app.core.normalize.sam import normalized_solicitation
from app.core.opportunity import OpportunityStatus
from app.core.status import DERIVED_STATUSES, derived_status
from app.models import Opportunity, OpportunityDocument, OpportunityVersion
from app.services.dedupe import Merge, dedupe
from app.services.events import OPPORTUNITY_AMENDED, OPPORTUNITY_CREATED, EventBus, get_event_bus
from app.services.status_job import propagate_terminal_status

log = structlog.get_logger(__name__)

# Columns copied 1:1 from OpportunityIn onto the row.
DIRECT_FIELDS: tuple[str, ...] = (
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
    "eligibility",
)


@dataclass(slots=True)
class IngestResult:
    opportunity: Opportunity
    created: bool
    changed: bool
    version: int
    content_hash: str
    diff: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    event: str | None = None
    merged: list[Merge] = field(default_factory=list)

    @property
    def unchanged(self) -> bool:
        return not (self.created or self.changed)


def _row_values(row: Opportunity, documents: list[OpportunityDocument]) -> dict[str, Any]:
    """The row as an OpportunityIn-shaped mapping for canonical_payload()."""
    values: dict[str, Any] = {
        "source_id": row.source_id,
        "external_id": row.external_id,
        **{name: getattr(row, name) for name in DIRECT_FIELDS},
        "place_of_performance": row.place_of_performance,
        "contacts": row.contacts,
        "status": row.status,
        "extra": row.extra,
        "documents": [
            {"url": d.url, "file_name": d.file_name, "kind": d.kind, "sha256": d.hash}
            for d in documents
        ],
    }
    return values


def _apply(
    row: Opportunity, opp: OpportunityIn, settings: Settings, *, new: bool, now: datetime
) -> None:
    for name in DIRECT_FIELDS:
        setattr(row, name, getattr(opp, name))
    row.place_of_performance = (
        opp.place_of_performance.model_dump(mode="json") if opp.place_of_performance else None
    )
    row.contacts = [c.model_dump(mode="json") for c in opp.contacts]
    extra = {k: v for k, v in opp.extra.items() if k != "raw_ref"}
    previous = getattr(row, "extra", None) or {}
    if "also_from" in previous:  # dedupe bookkeeping (M2-10) survives re-ingest
        extra["also_from"] = previous["also_from"]
    row.extra = extra
    row.reference_norm = normalized_reference(opp.solicitation_number)
    row.buyer_norm = normalized_buyer(opp.buyer_org)
    row.detail_status = opp.detail_status.value
    row.estimated_value_min_usd = to_usd(opp.estimated_value_min, opp.currency, settings.fx_rates)
    row.estimated_value_max_usd = to_usd(opp.estimated_value_max, opp.currency, settings.fx_rates)
    if opp.status is not None:
        row.status = opp.status
    elif new or OpportunityStatus(row.status) in DERIVED_STATUSES:
        # No source-stated status: derive it from the deadline (SPEC 5.4). Terminal
        # statuses (cancelled/awarded) stay until the source says otherwise.
        row.status = derived_status(opp.response_due_at, now)


async def _existing(session: AsyncSession, opp: OpportunityIn) -> Opportunity | None:
    stmt = (
        select(Opportunity)
        .options(selectinload(Opportunity.documents))
        .where(Opportunity.source_id == opp.source_id, Opportunity.external_id == opp.external_id)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _sync_documents(
    session: AsyncSession,
    row: Opportunity,
    opp: OpportunityIn,
    existing: list[OpportunityDocument],
) -> list[OpportunityDocument]:
    """Add newly listed documents (pending), refresh names/mime of known ones. Documents
    that disappeared from the listing are kept (they were public once)."""
    by_url = {d.url: d for d in existing}
    for ref in opp.documents:
        doc = by_url.get(ref.url)
        if doc is None:
            doc = OpportunityDocument(
                opportunity_id=row.id,
                url=ref.url,
                file_name=ref.file_name,
                kind=ref.kind.value,
                mime_type=ref.mime_type,
                size=ref.size,
                hash=ref.sha256,
            )
            session.add(doc)
            by_url[ref.url] = doc
            continue
        if ref.file_name and not doc.file_name:
            doc.file_name = ref.file_name
        if ref.mime_type and not doc.mime_type:
            doc.mime_type = ref.mime_type
        if ref.size and not doc.size:
            doc.size = ref.size
        if ref.sha256 and not doc.hash:
            doc.hash = ref.sha256
    return list(by_url.values())


async def resolve_parent(
    session: AsyncSession, opp: OpportunityIn, self_id: uuid.UUID | None
) -> uuid.UUID | None:
    """parent_opportunity_id: the explicit parent_external_id, else the earliest notice
    from the same source with the same (normalised) solicitation number."""
    if opp.parent_external_id:
        parent_id = (
            await session.execute(
                select(Opportunity.id).where(
                    Opportunity.source_id == opp.source_id,
                    Opportunity.external_id == opp.parent_external_id,
                )
            )
        ).scalar_one_or_none()
        if parent_id is not None and parent_id != self_id:
            return parent_id
    key = normalized_solicitation(opp.solicitation_number)
    if not key:
        return None
    stmt = (
        select(Opportunity.id, Opportunity.posted_at)
        .where(
            Opportunity.source_id == opp.source_id,
            func.regexp_replace(func.upper(Opportunity.solicitation_number), "[^A-Z0-9]", "", "g")
            == key,
        )
        .order_by(Opportunity.posted_at.asc().nulls_last(), Opportunity.created_at.asc())
    )
    for candidate_id, posted_at in (await session.execute(stmt)).all():
        if candidate_id == self_id:
            continue
        if opp.posted_at and posted_at and posted_at > opp.posted_at:
            # Every other notice is later than this one: this one is the root.
            return None
        parent: uuid.UUID = candidate_id
        return parent
    return None


async def ingest(
    session: AsyncSession,
    opp: OpportunityIn,
    raw_ref: str | None = None,
    *,
    now: datetime | None = None,
    bus: EventBus | None = None,
    settings: Settings | None = None,
) -> IngestResult:
    now = now or datetime.now(UTC)
    bus = bus or get_event_bus()
    settings = settings or get_settings()
    new_hash = content_hash(opp)
    row = await _existing(session, opp)

    if row is None:
        row = Opportunity(source_id=opp.source_id, external_id=opp.external_id, version=1)
        _apply(row, opp, settings, new=True, now=now)
        row.content_hash = new_hash
        row.raw_ref = raw_ref or opp.extra.get("raw_ref")
        row.last_seen_at = now
        session.add(row)
        await session.flush()
        _sync_documents(session, row, opp, [])
        row.parent_opportunity_id = await resolve_parent(session, opp, row.id)
        await session.flush()
        merged = await dedupe(session, row)
        await propagate_terminal_status(session, row, bus=bus)
        await bus.publish(
            OPPORTUNITY_CREATED,
            {
                "opportunity_id": str(row.id),
                "source_id": row.source_id,
                "external_id": row.external_id,
                "version": 1,
                "parent_opportunity_id": (
                    str(row.parent_opportunity_id) if row.parent_opportunity_id else None
                ),
                "duplicate_of": str(row.duplicate_of) if row.duplicate_of else None,
            },
        )
        return IngestResult(
            row,
            created=True,
            changed=False,
            version=1,
            content_hash=new_hash,
            event=OPPORTUNITY_CREATED,
            merged=merged,
        )

    row.last_seen_at = now
    if raw_ref:
        row.raw_ref = raw_ref
    if row.parent_opportunity_id is None:
        row.parent_opportunity_id = await resolve_parent(session, opp, row.id)
    if row.content_hash == new_hash:
        await session.flush()
        return IngestResult(
            row, created=False, changed=False, version=row.version, content_hash=new_hash
        )

    before = canonical_payload(_row_values(row, list(row.documents)))
    _apply(row, opp, settings, new=False, now=now)
    documents = _sync_documents(session, row, opp, list(row.documents))
    await session.flush()
    after = canonical_payload(_row_values(row, documents))
    diff = diff_payloads(before, after)
    changes = classify_changes(diff)
    row.version += 1
    row.content_hash = new_hash
    session.add(
        OpportunityVersion(
            opportunity_id=row.id,
            version=row.version,
            diff=diff,
            changes=changes,
            content_hash=new_hash,
        )
    )
    await session.flush()
    merged = await dedupe(session, row)
    if "status" in diff:
        await propagate_terminal_status(session, row, bus=bus)
    await bus.publish(
        OPPORTUNITY_AMENDED,
        {
            "opportunity_id": str(row.id),
            "source_id": row.source_id,
            "external_id": row.external_id,
            "version": row.version,
            "changes": changes,
            "diff": diff,
            "parent_opportunity_id": (
                str(row.parent_opportunity_id) if row.parent_opportunity_id else None
            ),
            "duplicate_of": str(row.duplicate_of) if row.duplicate_of else None,
        },
    )
    log.info(
        "opportunity.amended", opportunity_id=str(row.id), version=row.version, changes=changes
    )
    return IngestResult(
        row,
        created=False,
        changed=True,
        version=row.version,
        content_hash=new_hash,
        diff=diff,
        changes=changes,
        event=OPPORTUNITY_AMENDED,
        merged=merged,
    )


async def ingest_sink(session: AsyncSession, opp: OpportunityIn, raw: RawRecord) -> bool:
    """run_source sink: ingest and report whether anything was written."""
    result = await ingest(session, opp, raw_ref=raw.raw_ref)
    return result.created or result.changed
