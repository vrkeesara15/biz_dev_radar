"""Opportunity documents: parse bytes, store the text, write page-tagged chunks (M2-12).

    doc = await parse_and_store(session, document, data, storage=storage, ocr=ocr)

- opportunity_documents: hash (sha256 of the bytes), size, pages, status parsed|failed,
  parsed_text_ref = object key parsed/{opportunity_id}/{document_id}.txt (pages joined
  with form feeds, so `text.split("\\f")` gives the pages back)
- document_chunks: replaced on every parse; embedding stays NULL until M4 embeds them

`download_document` fetches the bytes through the polite HTTP client (raw copy archived
under raw/<source>/... like every other fetch).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.http import PoliteClient
from app.core.parsing import OCR, ParsedDocument, ParseError, chunk_pages, parse_document
from app.core.paths import parsed_text_key
from app.models import DocumentChunk, Opportunity, OpportunityDocument
from app.services.storage import Storage

log = structlog.get_logger(__name__)

STATUS_PARSED = "parsed"
STATUS_FAILED = "failed"
STATUS_DOWNLOADED = "downloaded"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class DocumentTooLargeError(ValueError):
    pass


def download_document(document: OpportunityDocument, client: PoliteClient) -> bytes:
    """GET the document through the polite client (rate limits, robots, raw archive)."""
    opportunity: Opportunity | None = document.opportunity
    source_id = opportunity.source_id if opportunity is not None else "documents"
    response = client.get(
        document.url, source_id=source_id, external_id=f"doc-{document.id}", archive=True
    )
    if response.status_code >= 400:
        raise ParseError(f"download failed with HTTP {response.status_code}")
    data = response.content
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLargeError(f"{len(data)} bytes > {MAX_DOCUMENT_BYTES}")
    if not document.mime_type:
        document.mime_type = response.response.headers.get("content-type", "").split(";")[0] or None
    return data


async def parse_and_store(
    session: AsyncSession,
    document: OpportunityDocument,
    data: bytes,
    *,
    storage: Storage,
    ocr: OCR | None = None,
    languages: str | None = None,
) -> ParsedDocument | None:
    """Parse `data`, persist text + chunks and update the row. Returns None on failure
    (the row is marked failed with the reason in `parse_error`)."""
    kwargs: dict[str, Any] = {"file_name": document.file_name, "mime_type": document.mime_type}
    if languages:
        kwargs["languages"] = languages
    try:
        parsed = parse_document(data, ocr=ocr, **kwargs)
    except ParseError as exc:
        document.status = STATUS_FAILED
        document.parse_error = str(exc)[:500]
        document.size = len(data)
        await session.flush()
        log.warning("document.parse_failed", document_id=str(document.id), error=str(exc))
        return None

    key = parsed_text_key(document.opportunity_id, document.id)
    await storage.put(key, parsed.text.encode("utf-8"), TEXT_CONTENT_TYPE)
    document.hash = parsed.sha256
    document.size = len(data)
    document.pages = parsed.page_count
    document.status = STATUS_PARSED
    document.parsed_text_ref = key
    document.parse_error = None
    document.ocr_pages = parsed.ocr_pages
    document.mime_type = _mime_for(parsed.kind)  # the bytes are authoritative

    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    for chunk in chunk_pages(parsed.pages):
        session.add(
            DocumentChunk(
                document_id=document.id, chunk_index=chunk.index, page=chunk.page, text=chunk.text
            )
        )
    await session.flush()
    log.info(
        "document.parsed",
        document_id=str(document.id),
        parser=parsed.parser,
        pages=parsed.page_count,
        ocr_pages=parsed.ocr_pages,
        warnings=parsed.warnings,
    )
    return parsed


async def load_parsed_text(storage: Storage, document: OpportunityDocument) -> list[str]:
    """The stored pages of a parsed document ([] when not parsed)."""
    if not document.parsed_text_ref:
        return []
    return (await storage.get(document.parsed_text_ref)).decode("utf-8").split("\f")


def _mime_for(kind: str) -> str:
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[kind]


def document_key(opportunity_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return parsed_text_key(opportunity_id, document_id)
