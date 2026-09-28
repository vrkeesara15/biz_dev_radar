"""M5-12 (SPEC 11): an agent run may only read and write its OWN pursuit's records.

The static half is tests/unit/test_agent_tool_scope.py; this is the runtime half --
`PursuitScope` on every StepContext, `ScopeViolation` on a write that aims elsewhere, and
the three read-only tools refusing another pursuit's document.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agents.pipeline import STEP_MODULES
from app.agents.runner import AgentRunner, StepContext, StepSpec
from app.agents.services import AgentServices
from app.agents.tools import (
    DEFAULT_KB_K,
    PursuitScope,
    PursuitTools,
    ScopeViolation,
    ScopeViolationError,
    tool_names,
)
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.models import (
    CompanyProfile,
    Opportunity,
    OpportunityDocument,
    Pursuit,
    Requirement,
)
from app.services.documents import parse_and_store
from app.services.drafts import add_comment, create_task, save_version
from app.services.knowledge_base import index_profile
from app.services.pursuits import store_artifact
from app.services.scanner import NoopScanner
from app.services.storage import StorageRouter

from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
PDF = b"%PDF-1.4 minimal"


def _services() -> AgentServices:
    return AgentServices(settings=SETTINGS, storage=StorageRouter(SETTINGS), scanner=NoopScanner())


async def _two_pursuits(database: Database, fake_embeddings: Any) -> dict[str, Any]:
    """One tenant with TWO pursuits: RLS cannot tell them apart, only the scope can."""
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Alpha Federal LLC"
        )
        session.add(profile)
        await session.flush()
        ids: dict[str, Any] = {"tenant_id": tenant.id, "user_id": user.id, "profile_id": profile.id}
        for name in ("a", "b"):
            opp = Opportunity(
                source_id="sam_opps",
                external_id=f"ext-{uuid.uuid4().hex[:8]}",
                region=Region.US,
                country="US",
                currency="USD",
                notice_type=NoticeType.RFP,
                title=f"Notice {name}",
            )
            session.add(opp)
            await session.flush()
            doc = OpportunityDocument(
                opportunity_id=opp.id, url=f"https://x.test/{name}.pdf", file_name=f"{name}.pdf"
            )
            session.add(doc)
            await session.flush()
            pursuit = Pursuit(
                tenant_id=tenant.id,
                profile_id=profile.id,
                opportunity_id=opp.id,
                created_by=user.id,
                decision="bid",
                stage="drafting",
            )
            session.add(pursuit)
            await session.flush()
            session.add(
                Requirement(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    req_id="R-001",
                    text=f"Requirement of pursuit {name}.",
                    document_id=doc.id,
                    page=1,
                    type="shall",
                    quote=f"requirement of pursuit {name}",
                )
            )
            ids[f"pursuit_{name}"] = pursuit.id
            ids[f"document_{name}"] = doc.id
            ids[f"opportunity_{name}"] = opp.id
        await session.flush()
    async with database.session(ids["tenant_id"]) as session:
        await index_profile(session, ids["profile_id"], embeddings=fake_embeddings)
    return ids


# --- the scope object --------------------------------------------------------------------


def test_the_scope_names_one_tenant_and_one_pursuit() -> None:
    tenant, pursuit, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scope = PursuitScope(tenant_id=tenant, pursuit_id=pursuit, run_id=uuid.uuid4())
    assert scope.require_tenant(tenant) == tenant
    assert scope.require_pursuit(pursuit) == pursuit
    with pytest.raises(ScopeViolation, match="scoped to tenant"):
        scope.require_tenant(other)
    with pytest.raises(ScopeViolation, match="scoped to pursuit"):
        scope.require_pursuit(other)
    scope.check(tenant_id=tenant, pursuit_id=pursuit)
    with pytest.raises(ScopeViolation):
        scope.check(pursuit_id=other)
    assert ScopeViolation is ScopeViolationError


def test_a_run_with_no_pursuit_may_not_write_one() -> None:
    scope = PursuitScope(tenant_id=uuid.uuid4())
    with pytest.raises(ScopeViolation, match="has no pursuit"):
        scope.require_pursuit(uuid.uuid4())


# --- the runner hands every step a scope --------------------------------------------------


async def test_the_runner_scopes_every_step_to_its_own_run(
    database: Database, fake_embeddings: Any
) -> None:
    ids = await _two_pursuits(database, fake_embeddings)
    seen: dict[str, Any] = {}

    async def step(ctx: StepContext) -> dict[str, str]:
        assert ctx.scope is not None
        seen["scope"] = ctx.scope
        seen["tools"] = tool_names(ctx.tools())
        return {"ok": "yes"}

    runner = AgentRunner(database, tenant_id=ids["tenant_id"], llm=FakeLLM(), services=_services())
    run_id = await runner.start(kind="pipeline", pursuit_id=ids["pursuit_a"])
    result = await runner.run(run_id, [StepSpec("collect", step)])
    assert result.status == "done"
    scope: PursuitScope = seen["scope"]
    assert scope.tenant_id == ids["tenant_id"]
    assert scope.pursuit_id == ids["pursuit_a"] and scope.run_id == run_id
    assert seen["tools"] == ("kb_search", "read_document", "read_requirements")


@pytest.mark.parametrize(
    "write",
    ["save_version", "create_task", "add_comment", "store_artifact"],
)
async def test_a_step_writing_to_another_pursuit_raises_scope_violation(
    database: Database, fake_embeddings: Any, write: str
) -> None:
    ids = await _two_pursuits(database, fake_embeddings)
    captured: dict[str, Any] = {}

    async def step(ctx: StepContext) -> None:
        other = ids["pursuit_b"]  # a pursuit of the SAME tenant: RLS allows it, the scope must not
        if write == "save_version":
            await save_version(
                ctx.session,
                ctx.tenant_id,
                other,
                "technical-approach",
                title="Technical Approach",
                body_html="<p>stolen</p>",
                scope=ctx.scope,
            )
        elif write == "create_task":
            await create_task(ctx.session, ctx.tenant_id, other, title="x", scope=ctx.scope)
        elif write == "add_comment":
            await add_comment(
                ctx.session,
                ctx.tenant_id,
                other,
                target_type="draft_section",
                target_id=uuid.uuid4(),
                body="x",
                scope=ctx.scope,
            )
        else:
            await store_artifact(ctx.session, ctx.tenant_id, other, "outline", {}, scope=ctx.scope)

    async def capture(ctx: StepContext) -> None:
        captured["scope"] = ctx.scope
        await step(ctx)

    runner = AgentRunner(database, tenant_id=ids["tenant_id"], llm=FakeLLM(), services=_services())
    run_id = await runner.start(kind="pipeline", pursuit_id=ids["pursuit_a"])
    result = await runner.run(run_id, [StepSpec("collect", capture)])
    assert result.status == "failed"
    assert "ScopeViolationError" in (result.error or "")
    assert "scoped to pursuit" in (result.error or "")


async def test_the_same_write_inside_the_scope_succeeds(
    database: Database, fake_embeddings: Any
) -> None:
    ids = await _two_pursuits(database, fake_embeddings)

    async def step(ctx: StepContext) -> dict[str, str]:
        assert ctx.run.pursuit_id is not None
        _draft, version = await save_version(
            ctx.session,
            ctx.tenant_id,
            ctx.run.pursuit_id,
            "technical-approach",
            title="Technical Approach",
            body_html="<p>Our own section.</p>",
            scope=ctx.scope,
        )
        await create_task(
            ctx.session, ctx.tenant_id, ctx.run.pursuit_id, title="ask the owner", scope=ctx.scope
        )
        await store_artifact(
            ctx.session, ctx.tenant_id, ctx.run.pursuit_id, "outline", {}, scope=ctx.scope
        )
        return {"version": str(version.version)}

    runner = AgentRunner(database, tenant_id=ids["tenant_id"], llm=FakeLLM(), services=_services())
    run_id = await runner.start(kind="pipeline", pursuit_id=ids["pursuit_a"])
    result = await runner.run(run_id, [StepSpec("collect", step)])
    assert result.status == "done", result.error
    assert result.outputs["collect"] == {"version": "1"}


async def test_a_write_aimed_at_another_tenant_raises_too(
    database: Database, fake_embeddings: Any
) -> None:
    ids = await _two_pursuits(database, fake_embeddings)
    scope = PursuitScope(tenant_id=ids["tenant_id"], pursuit_id=ids["pursuit_a"])
    async with database.session(ids["tenant_id"]) as session:
        with pytest.raises(ScopeViolation, match="scoped to tenant"):
            await store_artifact(
                session, uuid.uuid4(), ids["pursuit_a"], "outline", {}, scope=scope
            )


# --- the read-only tools --------------------------------------------------------------------


async def test_the_tools_read_only_inside_the_scope(
    database: Database, fake_embeddings: Any
) -> None:
    ids = await _two_pursuits(database, fake_embeddings)
    async with database.session(ids["tenant_id"]) as session:
        scope = PursuitScope(tenant_id=ids["tenant_id"], pursuit_id=ids["pursuit_a"])
        tools = PursuitTools(session=session, scope=scope)

        requirements = await tools.read_requirements()
        assert [r.req_id for r in requirements] == ["R-001"]
        assert requirements[0].text.endswith("pursuit a.")
        assert requirements[0].page == 1

        opportunity = await tools.opportunity()
        assert opportunity.id == ids["opportunity_a"]

        hits = await tools.kb_search("cloud migration", k=DEFAULT_KB_K)
        assert all(hit.chunk.profile_id == ids["profile_id"] for hit in hits)
        assert await tools.kb_search("", k=0) == []

        # the other pursuit's document is not readable through the tool
        with pytest.raises(ScopeViolation, match="does not belong to pursuit"):
            await tools.read_document(ids["document_b"])
        # and neither is an unknown id
        with pytest.raises(ScopeViolation):
            await tools.read_document(uuid.uuid4())


async def test_read_document_returns_one_page_of_the_pursuits_own_document(
    database: Database, fake_embeddings: Any
) -> None:
    ids = await _two_pursuits(database, fake_embeddings)
    storage = StorageRouter(SETTINGS).for_region(Region.US)
    async with database.owner_session() as session:
        document = await session.get(OpportunityDocument, ids["document_a"])
        assert document is not None
        await parse_and_store(
            session,
            document,
            _pdf_bytes(),
            storage=storage,
        )
    async with database.session(ids["tenant_id"]) as session:
        tools = PursuitTools(
            session=session,
            scope=PursuitScope(tenant_id=ids["tenant_id"], pursuit_id=ids["pursuit_a"]),
            storage=storage,
        )
        page = await tools.read_document(ids["document_a"], page=1)
        assert page.page == 1 and page.pages >= 1
        assert "Statement of work" in page.text
        with pytest.raises(LookupError):
            await tools.read_document(ids["document_a"], page=99)
        # without storage the tool refuses rather than guessing
        bare = PursuitTools(
            session=session,
            scope=PursuitScope(tenant_id=ids["tenant_id"], pursuit_id=ids["pursuit_a"]),
        )
        with pytest.raises(ScopeViolation, match="needs the region's storage"):
            await bare.read_document(ids["document_a"])


async def test_a_run_without_a_pursuit_has_nothing_to_read(database: Database) -> None:
    async with database.owner_session() as session:
        tenant, _user, _ = await create_tenant_with_owner(session)
    async with database.session(tenant.id) as session:
        tools = PursuitTools(session=session, scope=PursuitScope(tenant_id=tenant.id))
        with pytest.raises(ScopeViolation, match="no pursuit"):
            await tools.read_requirements()


def test_every_registered_agent_module_is_covered_by_the_policy() -> None:
    """A new pipeline module must be listed so the AST scan sees it."""
    from tests.unit.test_agent_tool_scope import agent_modules, module_name

    scanned = {module_name(p) for p in agent_modules()}
    assert set(STEP_MODULES) <= scanned


def _pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Statement of work for pursuit a.")
    data: bytes = doc.tobytes()
    doc.close()
    return data
