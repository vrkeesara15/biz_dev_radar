"""Opportunity embeddings (SPEC 5.3 `embedding vector(1024)` on title + summary +
requirements; M4 semantic-similarity signal).

    ok = await embed_opportunity(session, opportunity, embeddings=provider)
    embedder = install_opportunity_embeddings(settings, bus)     # created / amended events

The text is the title, summary_ai (when present) and the first requirements/description
chunk: description_text capped at one chunk, else the first page-tagged chunk of the first
parsed document. The subscriber runs after the summary enricher on the same event (bus
handlers run in subscription order), inside the ingest transaction via
event.context["session"], so the vector commits with the notice.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.parsing import CHUNK_CHARS
from app.models import DocumentChunk, Opportunity, OpportunityDocument
from app.services.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    embeddings_available,
    embeddings_from_settings,
)
from app.services.events import OPPORTUNITY_AMENDED, OPPORTUNITY_CREATED, Event, EventBus

log = structlog.get_logger(__name__)


def opportunity_text(row: Opportunity, first_chunk: str | None = None) -> str:
    parts = [row.title.strip()]
    if row.summary_ai:
        parts.append(row.summary_ai.strip())
    body = (row.description_text or "").strip()[:CHUNK_CHARS] or (first_chunk or "").strip()
    if body:
        parts.append(body[:CHUNK_CHARS])
    return "\n\n".join(p for p in parts if p)


async def first_document_chunk(session: AsyncSession, opportunity_id: uuid.UUID) -> str | None:
    row = await session.execute(
        select(DocumentChunk.text)
        .join(OpportunityDocument, OpportunityDocument.id == DocumentChunk.document_id)
        .where(OpportunityDocument.opportunity_id == opportunity_id)
        .order_by(OpportunityDocument.created_at, OpportunityDocument.id, DocumentChunk.chunk_index)
        .limit(1)
    )
    text = row.scalar_one_or_none()
    return str(text) if text else None


async def embed_opportunity(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    embeddings: EmbeddingProvider,
) -> bool:
    """Fill opportunities.embedding; False (row untouched) when the provider fails."""
    first_chunk = None
    if not (opportunity.description_text or "").strip():
        first_chunk = await first_document_chunk(session, opportunity.id)
    text = opportunity_text(opportunity, first_chunk)
    try:
        vector = (await embeddings.embed([text], input_type="document"))[0]
    except EmbeddingError as exc:
        log.warning("opportunity.embed_failed", opportunity_id=str(opportunity.id), error=str(exc))
        return False
    opportunity.embedding = vector
    await session.flush()
    return True


class OpportunityEmbedder:
    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self.embeddings = embeddings
        self.embedded: list[uuid.UUID] = []

    def subscribe(self, bus: EventBus) -> OpportunityEmbedder:
        bus.subscribe(OPPORTUNITY_CREATED, self.on_event)
        bus.subscribe(OPPORTUNITY_AMENDED, self.on_event)
        return self

    async def on_event(self, event: Event) -> None:
        session: AsyncSession | None = event.context.get("session")
        if session is None:
            log.warning("opportunity_embeddings.no_session", event=event.name)
            return
        opportunity_id = uuid.UUID(event.payload["opportunity_id"])
        row = await session.get(Opportunity, opportunity_id)
        if row is None:
            return
        if await embed_opportunity(session, row, embeddings=self.embeddings):
            self.embedded.append(row.id)


def install_opportunity_embeddings(
    settings: Settings | None,
    bus: EventBus,
    *,
    embeddings: EmbeddingProvider | None = None,
) -> OpportunityEmbedder | None:
    """Subscribe the embedder when a provider can work here (Voyage needs its key)."""
    settings = settings or get_settings()
    provider: Any = embeddings or embeddings_from_settings(settings)
    if not embeddings_available(provider):
        log.info("opportunity_embeddings.disabled", reason="embedding provider not configured")
        return None
    return OpportunityEmbedder(provider).subscribe(bus)
