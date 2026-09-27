"""Agent 1: document collector (SPEC 8). Registered as pipeline step "collect".

For the pursuit's opportunity: every opportunity_documents row (plus the adapter's
`fetch_documents` when the notice has none yet) is downloaded through the polite HTTP
client, hashed, virus-scanned, parsed to page-numbered text with sections (OCR fallback for
scanned pages) and chunked (services.documents.parse_and_store). Unchanged hashes are
skipped, infected files are marked and become a task note, CAPTCHA / login-walled sources
(detail_status manual, 401/403, robots) become manual-download items. The step output
(CollectorOutput) is stored on the agent_steps row; opportunity_documents carries the
per-document state.

No LLM: the step has no cost estimator, so the cost guard treats it as free.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.adapters import registry
from app.adapters.http import PoliteClientError, RobotsDisallowedError
from app.adapters.registry import AdapterNotFoundError
from app.agents.pipeline import STEP_COLLECT, register
from app.agents.runner import StepContext
from app.core.opportunity import DetailStatus, DocumentRef
from app.core.parsing import ParseError
from app.models import Opportunity, OpportunityDocument, Pursuit
from app.services.documents import (
    STATUS_PARSED,
    DocumentTooLargeError,
    download_document,
    parse_and_store,
)

log = structlog.get_logger(__name__)

STATUS_INFECTED = "infected"
STATUS_FAILED = "failed"
STATUS_UNCHANGED = "unchanged"
STATUS_MANUAL = "manual"
MANUAL_HTTP = frozenset({401, 403, 407, 429})


class DocumentReport(BaseModel):
    document_id: uuid.UUID
    file_name: str | None
    url: str
    sha256: str | None
    pages: int | None
    status: str  # parsed | unchanged | failed | infected | manual
    sections_count: int
    ocr_pages: int = 0
    error: str | None = None


class ManualItem(BaseModel):
    """Something a human must download from the portal (SPEC 5.1: never bypass walls)."""

    url: str
    file_name: str | None = None
    reason: str


class TaskNote(BaseModel):
    """Becomes a pursuit task once the tasks table lands (M6); stored with the output."""

    title: str
    detail: str
    document_id: uuid.UUID | None = None


class CollectorOutput(BaseModel):
    opportunity_id: uuid.UUID
    documents: list[DocumentReport] = Field(default_factory=list)
    manual_items: list[ManualItem] = Field(default_factory=list)
    tasks: list[TaskNote] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def parsed_count(self) -> int:
        return sum(1 for d in self.documents if d.status in (STATUS_PARSED, STATUS_UNCHANGED))


async def _opportunity(ctx: StepContext) -> Opportunity:
    if ctx.run.pursuit_id is None:
        raise RuntimeError("collect needs a pursuit")
    pursuit = await ctx.session.get(Pursuit, ctx.run.pursuit_id)
    if pursuit is None:
        raise LookupError(f"pursuit {ctx.run.pursuit_id} not found")
    row = (
        await ctx.session.execute(
            select(Opportunity)
            .options(selectinload(Opportunity.documents).selectinload(OpportunityDocument.chunks))
            .where(Opportunity.id == pursuit.opportunity_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"opportunity {pursuit.opportunity_id} not found")
    return row


def discover_documents(opportunity: Opportunity) -> tuple[list[DocumentRef], str | None]:
    """Ask the source adapter for attachments when the notice carries none. Returns the
    refs and a warning when the adapter could not help (never fatal)."""
    try:
        cls = registry.get_adapter_class(opportunity.source_id)
    except AdapterNotFoundError:
        return [], f"no adapter registered for source {opportunity.source_id!r}"
    if not registry.is_enabled(cls):
        return [], f"source {opportunity.source_id!r} is a disabled stub"
    try:
        adapter = registry.create_adapter(opportunity.source_id)
        raw = adapter.fetch_detail(opportunity.external_id)
        return list(adapter.fetch_documents(raw)), None
    except Exception as exc:  # network / quota / shape problems: report, do not fail
        return [], f"fetch_documents failed for {opportunity.source_id}: {exc}"[:500]


async def _ensure_rows(ctx: StepContext, opportunity: Opportunity, refs: list[DocumentRef]) -> None:
    known = {d.url for d in opportunity.documents}
    for ref in refs:
        if ref.url in known:
            continue
        doc = OpportunityDocument(
            opportunity_id=opportunity.id,
            url=ref.url,
            file_name=ref.file_name,
            kind=ref.kind.value,
            mime_type=ref.mime_type,
            size=ref.size,
        )
        ctx.session.add(doc)
        opportunity.documents.append(doc)
        known.add(ref.url)
    await ctx.session.flush()


def _report(
    doc: OpportunityDocument, status: str, sections: int = 0, **extra: Any
) -> DocumentReport:
    return DocumentReport(
        document_id=doc.id,
        file_name=doc.file_name,
        url=doc.url,
        sha256=doc.hash,
        pages=doc.pages,
        status=status,
        sections_count=sections,
        ocr_pages=doc.ocr_pages or 0,
        **extra,
    )


@register(STEP_COLLECT)
async def collect(ctx: StepContext) -> CollectorOutput:
    services = ctx.require_services()
    opportunity = await _opportunity(ctx)
    output = CollectorOutput(opportunity_id=opportunity.id)
    ctx.step.input_ref = f"opportunity:{opportunity.id}@{opportunity.version}"

    if opportunity.detail_status == DetailStatus.MANUAL.value:
        output.manual_items.append(
            ManualItem(
                url=opportunity.source_url or "",
                reason="portal requires a login or CAPTCHA (detail_status manual): download the "
                "tender documents manually and upload them to the pursuit",
            )
        )
    if not opportunity.documents:
        refs, warning = discover_documents(opportunity)
        if warning:
            output.warnings.append(warning)
        await _ensure_rows(ctx, opportunity, refs)

    storage = services.storage_for(opportunity.region)
    client = services.http(opportunity.region)
    try:
        for doc in sorted(opportunity.documents, key=lambda d: (d.created_at, d.url)):
            output.documents.append(await _collect_one(ctx, doc, client, storage, output))
    finally:
        client.close()
    await ctx.session.flush()
    log.info(
        "collector.done",
        opportunity_id=str(opportunity.id),
        parsed=output.parsed_count,
        manual=len(output.manual_items),
        tasks=len(output.tasks),
    )
    return output


async def _collect_one(
    ctx: StepContext,
    doc: OpportunityDocument,
    client: Any,
    storage: Any,
    output: CollectorOutput,
) -> DocumentReport:
    services = ctx.require_services()
    try:
        data = download_document(doc, client)
    except RobotsDisallowedError as exc:
        return _manual(doc, output, f"robots.txt disallows fetching this file ({exc.url})")
    except DocumentTooLargeError as exc:
        return _failed(doc, f"too large: {exc}")
    except ParseError as exc:  # download_document raises ParseError for HTTP >= 400
        status_code = _http_status(str(exc))
        if status_code in MANUAL_HTTP:
            return _manual(doc, output, f"portal answered HTTP {status_code}: download manually")
        return _failed(doc, str(exc))
    except PoliteClientError as exc:
        return _failed(doc, str(exc))

    sha256 = hashlib.sha256(data).hexdigest()
    if doc.status == STATUS_PARSED and doc.hash == sha256 and doc.parsed_text_ref:
        return _report(doc, STATUS_UNCHANGED, sections=_sections_from_pages(doc))

    scan = await services.scanner.scan(data)
    if not scan.clean:
        doc.status = STATUS_INFECTED
        doc.hash = sha256
        doc.size = len(data)
        doc.parse_error = f"infected: {scan.signature or 'unknown signature'}"[:500]
        doc.parsed_text_ref = None
        await ctx.session.flush()
        output.tasks.append(
            TaskNote(
                title=f"Infected attachment: {doc.file_name or doc.url}",
                detail=(
                    f"{scan.scanner} reported {scan.signature or 'malware'}; the file was not "
                    "parsed. Verify the source and obtain a clean copy."
                ),
                document_id=doc.id,
            )
        )
        log.warning("collector.infected", document_id=str(doc.id), signature=scan.signature)
        return _report(doc, STATUS_INFECTED, error=doc.parse_error)

    parsed = await parse_and_store(
        ctx.session,
        doc,
        data,
        storage=storage,
        ocr=services.ocr,
        languages=services.settings.ocr_languages,
    )
    if parsed is None:
        return _report(doc, STATUS_FAILED, error=doc.parse_error)
    for warning in parsed.warnings:
        output.warnings.append(f"{doc.file_name or doc.url}: {warning}")
    return _report(doc, STATUS_PARSED, sections=len(parsed.sections))


def _failed(doc: OpportunityDocument, error: str) -> DocumentReport:
    doc.status = STATUS_FAILED
    doc.parse_error = error[:500]
    return _report(doc, STATUS_FAILED, error=doc.parse_error)


def _manual(doc: OpportunityDocument, output: CollectorOutput, reason: str) -> DocumentReport:
    doc.status = STATUS_FAILED
    doc.parse_error = reason[:500]
    output.manual_items.append(ManualItem(url=doc.url, file_name=doc.file_name, reason=reason))
    return _report(doc, STATUS_MANUAL, error=reason)


def _http_status(message: str) -> int | None:
    marker = "HTTP "
    if marker in message:
        digits = message.split(marker, 1)[1][:3]
        if digits.isdigit():
            return int(digits)
    return None


def _sections_from_pages(doc: OpportunityDocument) -> int:
    # sections are recomputed on read (OQ-45); an unchanged document keeps its chunk count
    # as the cheap proxy the UI shows
    return len(doc.chunks) if doc.chunks is not None else 0
