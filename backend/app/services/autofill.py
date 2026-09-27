"""Profile autofill service (M1-10, SPEC 4 / 10.3): suggestions from the company website,
an uploaded capability statement and the SAM.gov entity record. It NEVER writes the
profile; the human accepts each suggestion through the normal endpoints.

    response = await Autofiller(settings=..., database=..., llm=..., storage=...).run(
        session, profile, website_url=..., capability_file_id=..., uei=...
    )

- website: PoliteClient GET (robots.txt respected, 2 MB cap, HTML -> text) then one
  Haiku-class JSON extraction framed as untrusted content.
- capability PDF: Storage bytes -> core.parsing -> first pages -> same extraction; every
  suggestion's source_ref carries the file id and page.
- UEI: SAM.gov Entity Management API v3 (SAM_API_KEY) mapped deterministically at 0.95.

LLM calls are metered through AgentRunner under the caller's tenant (run kind
`profile_autofill`), so they count against the tenant's budget like every agent step.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.http import PoliteClient, PoliteClientError, PoliteResponse
from app.agents.autofill import document_blocks, extract_company_facts, website_blocks
from app.agents.llm import InvalidOutput, LLMClient, LLMError
from app.agents.runner import AgentRunner, StepContext, StepSpec
from app.agents.tracing import Tracer
from app.core.autofill import (
    AutofillExtraction,
    Suggestion,
    filter_for_region,
    map_sam_entity,
    suggestions_from_extraction,
)
from app.core.config import Settings
from app.core.db import Database
from app.core.html_text import html_title, html_to_text
from app.core.parsing import ParseError, parse_document
from app.models import CompanyProfile, File
from app.services.storage import StorageRouter

log = structlog.get_logger(__name__)

WEBSITE_MAX_BYTES = 2 * 1024 * 1024
WEBSITE_SOURCE_ID = "autofill_web"
SAM_SOURCE_ID = "sam_entity"
RUN_KIND = "profile_autofill"
TEXT_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
PARSEABLE_FILE_KINDS = frozenset({"pdf", "docx", "xlsx"})


class AutofillResponse(BaseModel):
    suggestions: list[Suggestion]
    warnings: list[str]


class Autofiller:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        llm: LLMClient | None,
        storage: StorageRouter,
        tracer: Tracer | None = None,
        client_factory: Callable[[], PoliteClient] | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.llm = llm
        self.storage = storage
        self.tracer = tracer
        self._client_factory = client_factory or (lambda: PoliteClient(settings=settings))

    # -- entry point -----------------------------------------------------------------------

    async def run(
        self,
        session: AsyncSession,
        profile: CompanyProfile,
        *,
        website_url: str | None = None,
        capability_file_id: uuid.UUID | None = None,
        uei: str | None = None,
    ) -> AutofillResponse:
        suggestions: list[Suggestion] = []
        warnings: list[str] = []
        with self._client_factory() as client:
            if website_url:
                found, notes = await self.from_website(client, profile, website_url)
                suggestions.extend(found)
                warnings.extend(notes)
            if capability_file_id is not None:
                found, notes = await self.from_capability(session, profile, capability_file_id)
                suggestions.extend(found)
                warnings.extend(notes)
            if uei:
                found, notes = await self.from_uei(client, uei)
                suggestions.extend(found)
                warnings.extend(notes)
        kept, dropped = filter_for_region(suggestions, profile.region)
        if dropped:
            fields = sorted({s.root for s in dropped})
            warnings.append(
                f"dropped suggestions for fields not available in region "
                f"{profile.region.value}: {', '.join(fields)}"
            )
        return AutofillResponse(suggestions=kept, warnings=warnings)

    # -- website ---------------------------------------------------------------------------

    async def from_website(
        self, client: PoliteClient, profile: CompanyProfile, url: str
    ) -> tuple[list[Suggestion], list[str]]:
        if self.llm is None:
            return [], ["website autofill needs an LLM (ANTHROPIC_API_KEY is not configured)"]
        host = urlsplit(url).netloc or "website"
        try:
            response: PoliteResponse = await asyncio.to_thread(
                client.get, url, source_id=WEBSITE_SOURCE_ID, external_id=host, archive=False
            )
        except PoliteClientError as exc:
            return [], [f"website: {exc}"]
        if response.status_code >= 400:
            return [], [f"website: HTTP {response.status_code} for {url}"]
        content_type = response.content_type.split(";")[0].strip().lower()
        if content_type and not content_type.startswith(TEXT_CONTENT_TYPES):
            return [], [f"website: {url} is {content_type}, not an HTML page"]
        warnings: list[str] = []
        body = response.content
        if len(body) > WEBSITE_MAX_BYTES:
            warnings.append(f"website: page larger than {WEBSITE_MAX_BYTES} bytes, truncated")
            body = body[:WEBSITE_MAX_BYTES]
        html = body.decode(response.response.encoding or "utf-8", errors="replace")
        text = html_to_text(html)
        if not text.strip():
            return [], [*warnings, f"website: no readable text at {url}"]
        extraction = await self._extract(
            profile,
            content=website_blocks(url, text, title=html_title(html)),
            task="the web page",
            input_ref=f"website:{url}",
            warnings=warnings,
        )
        if extraction is None:
            return [], warnings
        found = suggestions_from_extraction(
            extraction, source="website", source_ref=lambda _page: url
        )
        return found, warnings

    # -- capability statement --------------------------------------------------------------

    async def from_capability(
        self, session: AsyncSession, profile: CompanyProfile, file_id: uuid.UUID
    ) -> tuple[list[Suggestion], list[str]]:
        file = await session.get(File, file_id)  # RLS: another tenant's file is invisible
        if file is None:
            return [], [f"capability file {file_id} not found"]
        if self.llm is None:
            return [], ["capability autofill needs an LLM (ANTHROPIC_API_KEY is not configured)"]
        if file.kind not in PARSEABLE_FILE_KINDS:
            return [], [f"capability file {file.filename}: {file.kind} cannot be parsed"]
        data = await self.storage.for_region(file.region).get(file.key)
        try:
            parsed = parse_document(data, file_name=file.filename, mime_type=file.content_type)
        except ParseError as exc:
            return [], [f"capability file {file.filename}: {exc}"]
        pages = [page.text for page in parsed.pages]
        if not any(page.strip() for page in pages):
            return [], [f"capability file {file.filename}: no text layer (scanned? OCR not run)"]
        warnings: list[str] = list(parsed.warnings)
        extraction = await self._extract(
            profile,
            content=document_blocks(pages, name=file.filename),
            task="the capability statement",
            input_ref=f"file:{file.id}",
            warnings=warnings,
        )
        if extraction is None:
            return [], warnings

        def ref(page: int | None) -> str:
            return f"file:{file.id}#page={page}" if page else f"file:{file.id}"

        return suggestions_from_extraction(extraction, source="capability_pdf", source_ref=ref), (
            warnings
        )

    # -- UEI -------------------------------------------------------------------------------

    async def from_uei(self, client: PoliteClient, uei: str) -> tuple[list[Suggestion], list[str]]:
        if not self.settings.sam_api_key:
            return [], ["UEI lookup skipped: SAM_API_KEY is not configured"]
        try:
            response: PoliteResponse = await asyncio.to_thread(
                client.get,
                self.settings.sam_entity_api_url,
                source_id=SAM_SOURCE_ID,
                external_id=uei,
                params={"ueiSAM": uei, "api_key": self.settings.sam_api_key},
                archive=False,
                respect_robots=False,  # JSON API, not a crawl target
            )
        except PoliteClientError as exc:
            return [], [f"SAM.gov entity lookup failed: {exc}"]
        if response.status_code >= 400:
            return [], [f"SAM.gov entity lookup failed: HTTP {response.status_code}"]
        try:
            payload: Any = response.json()
        except ValueError:
            return [], ["SAM.gov entity lookup returned a non-JSON body"]
        return map_sam_entity(payload, uei=uei)

    # -- metered extraction ----------------------------------------------------------------

    async def _extract(
        self,
        profile: CompanyProfile,
        *,
        content: str,
        task: str,
        input_ref: str,
        warnings: list[str],
    ) -> AutofillExtraction | None:
        assert self.llm is not None

        async def step(ctx: StepContext) -> AutofillExtraction:
            result = await extract_company_facts(
                ctx.llm, settings=self.settings, content=content, task=task
            )
            parsed: AutofillExtraction = result.parsed
            return parsed

        runner = AgentRunner(
            self.database, tenant_id=profile.tenant_id, llm=self.llm, tracer=self.tracer
        )
        run_id = await runner.start(
            kind=RUN_KIND, params={"profile_id": str(profile.id), "input": input_ref}
        )
        result = await runner.run(run_id, [StepSpec("extract", step, input_ref=input_ref)])
        if result.status != "done":
            reason = result.error or "extraction failed"
            if InvalidOutput.__name__ in reason or LLMError.__name__ in reason:
                reason = reason.split(":", 1)[0]
            warnings.append(f"{task}: extraction failed ({reason})")
            log.warning("autofill.extraction_failed", input=input_ref, error=result.error)
            return None
        return AutofillExtraction.model_validate(result.outputs["extract"])
