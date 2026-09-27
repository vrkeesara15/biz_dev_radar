"""The only tool set an agent step may use (SPEC 11: "tool access for agents is limited
to read-only retrieval and writing to the pursuit's own records").

Two halves:

1. `PursuitScope` -- the tenant and pursuit a run is allowed to touch. The runner builds
   one from the `agent_runs` row and puts it on every `StepContext`; every write helper
   (`services.drafts.save_version` / `create_task` / `add_comment`,
   `services.pursuits.store_artifact`) takes it and raises `ScopeViolation` when the row
   it is about to write belongs to another pursuit or another tenant. RLS already stops
   a cross-TENANT write; the scope is what stops a cross-PURSUIT one inside a tenant.

2. `PursuitTools` -- the three read-only retrieval tools: `kb_search`, `read_document`
   and `read_requirements`. They are the only reads a prompt-driven tool loop would ever
   be given, and each is bound to the scope, so a document from another pursuit's
   opportunity is a `ScopeViolation` rather than a silent read.

The allowlists at the bottom are enforced by `tests/unit/test_agent_tool_scope.py`, which
parses every module in `app.agents` and fails on a write the list does not name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Opportunity, OpportunityDocument, Pursuit, Requirement
from app.services.documents import load_parsed_text
from app.services.knowledge_base import KBHit, similarity_search
from app.services.storage import Storage

DEFAULT_KB_K = 6
MAX_KB_K = 20


class ScopeViolationError(RuntimeError):
    """An agent step tried to read or write outside its own pursuit (SPEC 11)."""


#: the name SPEC 11 and the tests use; ruff's N818 wants the Error suffix on the class
ScopeViolation = ScopeViolationError


@dataclass(frozen=True, slots=True)
class PursuitScope:
    """What one agent run may touch: one tenant, at most one pursuit."""

    tenant_id: uuid.UUID
    pursuit_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None

    def require_tenant(self, tenant_id: uuid.UUID) -> uuid.UUID:
        if tenant_id != self.tenant_id:
            raise ScopeViolationError(
                f"run {self.run_id} is scoped to tenant {self.tenant_id}, not {tenant_id}"
            )
        return tenant_id

    def require_pursuit(self, pursuit_id: uuid.UUID) -> uuid.UUID:
        if self.pursuit_id is None:
            raise ScopeViolationError(
                f"run {self.run_id} has no pursuit and may not write pursuit {pursuit_id}"
            )
        if pursuit_id != self.pursuit_id:
            raise ScopeViolationError(
                f"run {self.run_id} is scoped to pursuit {self.pursuit_id}, not {pursuit_id}"
            )
        return pursuit_id

    def check(
        self, *, tenant_id: uuid.UUID | None = None, pursuit_id: uuid.UUID | None = None
    ) -> None:
        """Assert a write target is inside the scope. Used by the write helpers."""
        if tenant_id is not None:
            self.require_tenant(tenant_id)
        if pursuit_id is not None:
            self.require_pursuit(pursuit_id)


def enforce(
    scope: PursuitScope | None,
    *,
    tenant_id: uuid.UUID | None = None,
    pursuit_id: uuid.UUID | None = None,
) -> None:
    """`scope.check(...)` when a scope was given; a no-op for API callers, who are already
    authorised by the route's role check and bounded by RLS."""
    if scope is not None:
        scope.check(tenant_id=tenant_id, pursuit_id=pursuit_id)


# --- the read-only tools ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentPage:
    document_id: uuid.UUID
    file_name: str
    page: int
    text: str
    pages: int


@dataclass(frozen=True, slots=True)
class RequirementRow:
    req_id: str
    text: str
    type: str
    document_id: uuid.UUID
    page: int
    quote: str
    volume: str | None = None


@dataclass(slots=True)
class PursuitTools:
    """The read-only retrieval an agent may do. Nothing here writes."""

    session: AsyncSession
    scope: PursuitScope
    storage: Storage | None = None

    async def _pursuit(self) -> Pursuit:
        if self.scope.pursuit_id is None:
            raise ScopeViolationError("this run has no pursuit to read from")
        row = await self.session.get(Pursuit, self.scope.pursuit_id)
        if row is None:
            raise ScopeViolationError(f"pursuit {self.scope.pursuit_id} is not visible to this run")
        return row

    async def kb_search(self, query: str, k: int = DEFAULT_KB_K) -> list[KBHit]:
        """RAG over the pursuit's OWN profile only (SPEC 8: RAG indexes are per tenant)."""
        pursuit = await self._pursuit()
        return await similarity_search(
            self.session, pursuit.profile_id, query, k=max(0, min(int(k), MAX_KB_K))
        )

    async def read_document(self, document_id: uuid.UUID, page: int | None = None) -> DocumentPage:
        """One page of one document of THIS pursuit's opportunity."""
        pursuit = await self._pursuit()
        document = await self.session.get(OpportunityDocument, document_id)
        if document is None or document.opportunity_id != pursuit.opportunity_id:
            raise ScopeViolationError(
                f"document {document_id} does not belong to pursuit {pursuit.id}"
            )
        if self.storage is None:
            raise ScopeViolationError("read_document needs the region's storage")
        pages = await load_parsed_text(self.storage, document)
        index = 1 if page is None else int(page)
        if index < 1 or index > len(pages):
            raise LookupError(
                f"document {document_id} has {len(pages)} parsed page(s), not page {index}"
            )
        return DocumentPage(
            document_id=document.id,
            file_name=document.file_name or "",
            page=index,
            text=pages[index - 1],
            pages=len(pages),
        )

    async def read_requirements(self) -> list[RequirementRow]:
        """The requirements the extractor stored for this pursuit."""
        pursuit = await self._pursuit()
        rows = (
            await self.session.execute(
                select(Requirement)
                .where(Requirement.pursuit_id == pursuit.id)
                .order_by(Requirement.req_id)
            )
        ).scalars()
        return [
            RequirementRow(
                req_id=row.req_id,
                text=row.text,
                type=row.type,
                document_id=row.document_id,
                page=row.page,
                quote=row.quote,
                volume=row.volume,
            )
            for row in rows
        ]

    async def opportunity(self) -> Opportunity:
        """The notice this pursuit chases (read-only; everything in it is untrusted data)."""
        pursuit = await self._pursuit()
        row = await self.session.get(Opportunity, pursuit.opportunity_id)
        if row is None:  # pragma: no cover - the FK guarantees it
            raise ScopeViolationError(f"opportunity {pursuit.opportunity_id} is not visible")
        return row


TOOL_NAMES: tuple[str, ...] = ("kb_search", "read_document", "read_requirements")


def tool_names(tools: PursuitTools | None = None) -> tuple[str, ...]:
    """The tool names an agent may be offered. `tools` is accepted so a caller can prove
    the object really implements them."""
    if tools is not None:
        missing = [name for name in TOOL_NAMES if not callable(getattr(tools, name, None))]
        if missing:  # pragma: no cover - a refactor that drops a tool
            raise RuntimeError(f"PursuitTools is missing {missing}")
    return TOOL_NAMES


# --- the enforced allowlists ------------------------------------------------------------------
#
# tests/unit/test_agent_tool_scope.py parses every module under app/agents and fails when
# an agent writes through something these lists do not name. Adding an entry is a
# deliberate decision that the write is scoped to the run's own pursuit.

#: service functions an agent step may call to WRITE. Every one takes the pursuit id the
#: run owns and accepts a `scope` so `ScopeViolation` catches a mistake.
ALLOWED_WRITE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "save_version",  # drafts + draft_versions for this pursuit's sections
        "create_task",  # a [NEEDS INPUT] handed to a human
        "add_comment",  # a red-team finding on this pursuit's draft / matrix row
        "store_artifact",  # a versioned pursuit_artifacts row
        "parse_and_store",  # parsed text + chunks of this opportunity's documents
    }
)

#: models an agent module may insert / update / delete directly. Each is keyed on the
#: run's own pursuit (or on its opportunity, for the collector's documents).
ALLOWED_WRITE_MODELS: frozenset[str] = frozenset(
    {
        "OpportunityDocument",
        "Requirement",
        "ComplianceItem",
    }
)

#: the runner owns the run's own bookkeeping; no other agent module may write these.
RUNTIME_WRITE_MODULES: frozenset[str] = frozenset({"app.agents.runner"})
RUNTIME_WRITE_MODELS: frozenset[str] = frozenset({"AgentRun", "AgentStep", "UsageLedger"})

#: every name an app.agents module may import from app.services. A new entry has to be
#: justified against SPEC 11's "read-only retrieval and writes to the pursuit's own
#: records" -- that is the whole point of pinning the list.
ALLOWED_SERVICE_IMPORTS: frozenset[str] = frozenset(
    {
        # writes (the list above)
        "save_version",
        "create_task",
        "add_comment",
        "store_artifact",
        "parse_and_store",
        # read-only retrieval
        "load_parsed_text",
        "load_evidence",
        "load_snapshot",
        "load_match_opportunity",
        "load_match_profile",
        "similarity_search",
        "download_document",
        "evidence_by_token",
        "evidence_tokens",
        "render_evidence",
        # infrastructure protocols and helpers (no database writes of their own)
        "EvidenceRecord",
        "KBHit",
        "Scanner",
        "Storage",
        "StorageRouter",
        "PlanService",
        "DocumentTooLargeError",
        "STATUS_PARSED",
        "FANOUT_CELERY",
        "await_group",
        "enqueue_group",
        "fan_out",
        "ocr_from_settings",
        "scanner_from_settings",
    }
)

#: modules an agent may never import: the HTTP layer and anything that submits.
FORBIDDEN_AGENT_IMPORTS: tuple[str, ...] = ("app.api", "requests", "urllib.request")


__all__ = [
    "ALLOWED_SERVICE_IMPORTS",
    "ALLOWED_WRITE_FUNCTIONS",
    "ALLOWED_WRITE_MODELS",
    "DEFAULT_KB_K",
    "FORBIDDEN_AGENT_IMPORTS",
    "RUNTIME_WRITE_MODELS",
    "RUNTIME_WRITE_MODULES",
    "TOOL_NAMES",
    "DocumentPage",
    "PursuitScope",
    "PursuitTools",
    "RequirementRow",
    "ScopeViolation",
    "ScopeViolationError",
    "enforce",
    "tool_names",
]
