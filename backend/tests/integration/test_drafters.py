"""M5-08: one drafter per volume, RAG over the tenant's own knowledge base, citation
tokens that must resolve, [NEEDS INPUT] placeholders and the tasks they create."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.agents import pipeline
from app.agents.drafters import NEEDS_INPUT_EVIDENCE, DraftStepOutput, estimate_draft
from app.agents.prompting import UNTRUSTED_PREAMBLE
from app.agents.runner import AgentRunner, GuardContext
from app.agents.services import AgentServices
from app.core.citations import profile_token
from app.core.compliance import ARTIFACT_OUTLINE
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import BoilerplateKind, PerformanceRole
from app.models import (
    AgentRun,
    BoilerplateBlock,
    CompanyProfile,
    ComplianceItem,
    Draft,
    DraftVersion,
    KBChunk,
    Opportunity,
    OpportunityDocument,
    PastPerformance,
    Pursuit,
    PursuitArtifact,
    Requirement,
    Task,
)
from app.services.knowledge_base import index_profile
from app.services.scanner import NoopScanner
from app.services.storage import StorageRouter
from sqlalchemy import select

from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]

REQUIREMENTS: list[tuple[str, str, str]] = [
    ("R-001", "The contractor shall migrate 400 workloads to a FedRAMP Moderate cloud.", "shall"),
    ("R-002", "Offerors shall describe their quality management system.", "shall"),
    ("R-003", "Offerors shall provide three past performance references.", "shall"),
]

VOLUMES: list[dict[str, Any]] = [
    {
        "name": "Volume I - Technical",
        "sections": [
            {
                "id": "technical-approach",
                "title": "Technical Approach",
                "maps_requirements": ["R-001"],
                "evaluation_criterion": "Factor 1",
                "page_budget": 6,
            },
            {
                "id": "quality-management",
                "title": "Quality Management",
                "maps_requirements": ["R-002"],
                "page_budget": 2,
            },
        ],
    },
    {
        "name": "Volume II - Past Performance",
        "sections": [
            {
                "id": "past-performance",
                "title": "Past Performance",
                "maps_requirements": ["R-003"],
                "page_budget": 4,
            }
        ],
    },
]


@dataclass
class ScriptedLLM(FakeLLM):
    """FakeLLM that answers by section id, so concurrent volume drafters stay deterministic."""

    answers: dict[str, Any] = field(default_factory=dict)

    def _next(self) -> Any:
        user = self.calls[-1].user_text if self.calls else ""
        for section_id, payload in self.answers.items():
            if f"(id {section_id})" in user:
                return payload
        return super()._next()


def _services(**overrides: Any) -> AgentServices:
    settings = SETTINGS.model_copy(update=overrides) if overrides else SETTINGS
    return AgentServices(settings=settings, storage=StorageRouter(settings), scanner=NoopScanner())


async def _setup(
    database: Database, fake_embeddings: Any, *, volumes: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Alpha Federal")
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"ext-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
            source_url="https://sam.gov/opp/1",
        )
        session.add_all([profile, opp])
        await session.flush()
        past = PastPerformance(
            tenant_id=tenant.id,
            profile_id=profile.id,
            title="Treasury cloud migration",
            customer="US Treasury",
            role=PerformanceRole.PRIME,
            scope="Migrated 400 workloads to a FedRAMP Moderate cloud with zero downtime",
            outcomes="Delivered three months early",
        )
        block = BoilerplateBlock(
            tenant_id=tenant.id,
            profile_id=profile.id,
            kind=BoilerplateKind.QA_PLAN,
            title="Quality management system",
            body="<p>Our quality management system is audited annually.</p>",
        )
        doc = OpportunityDocument(
            opportunity_id=opp.id, url="https://x.test/rfp.pdf", file_name="rfp.pdf"
        )
        session.add_all([past, block, doc])
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
        for req_id, text, kind in REQUIREMENTS:
            req = Requirement(
                tenant_id=tenant.id,
                pursuit_id=pursuit.id,
                req_id=req_id,
                text=text,
                document_id=doc.id,
                page=3,
                type=kind,
                quote=f"verbatim: {text}",
            )
            session.add(req)
            await session.flush()
            session.add(
                ComplianceItem(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    requirement_id=req.id,
                    section="Technical Approach",
                    reason="keyword",
                )
            )
        session.add(
            PursuitArtifact(
                tenant_id=tenant.id,
                pursuit_id=pursuit.id,
                kind=ARTIFACT_OUTLINE,
                version=1,
                data={
                    "outline": {
                        "volumes": volumes if volumes is not None else VOLUMES,
                        "win_themes": [
                            {
                                "theme": "Zero-downtime migration",
                                "discriminator": "400 Treasury workloads, no outage",
                                "evidence_citations": [profile_token("past_performance", past.id)],
                            }
                        ],
                        "unmapped_requirements": [],
                    }
                },
            )
        )
        await session.flush()
    # index the knowledge base so the drafters have something to retrieve and cite
    async with database.session(tenant.id) as session:
        result = await index_profile(session, profile.id, embeddings=fake_embeddings)
        assert result.chunks > 0
    return {
        "tenant_id": tenant.id,
        "user_id": user.id,
        "profile_id": profile.id,
        "pursuit_id": pursuit.id,
        "past_performance": past.id,
        "boilerplate": block.id,
    }


async def _run(
    database: Database, ctx: dict[str, Any], llm: Any, *, services: AgentServices | None = None
) -> Any:
    specs, finish = pipeline.plan_steps("draft")
    assert [s.agent for s in specs] == ["draft"] and finish.status == "done"
    runner = AgentRunner(
        database, tenant_id=ctx["tenant_id"], llm=llm, services=services or _services()
    )
    run_id = await runner.start(
        kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "draft"}
    )
    return run_id, await runner.run(run_id, specs)


def _answer(body: str, *, citations: list[dict[str, str]], claims: list[dict[str, Any]]) -> dict:
    return {
        "body_markdown": body,
        "citations": citations,
        "needs_input": [],
        "claims": claims,
    }


async def test_one_drafter_per_volume_writes_grounded_versions(
    database: Database, fake_embeddings: Any
) -> None:
    ctx = await _setup(database, fake_embeddings)
    pp_token = profile_token("past_performance", ctx["past_performance"])
    bp_token = profile_token("boilerplate", ctx["boilerplate"])
    llm = ScriptedLLM(
        answers={
            "technical-approach": _answer(
                f"## Technical Approach\n\nWe migrated 400 workloads for the Treasury "
                f"[{pp_token}].",
                citations=[{"token": pp_token, "quote": "Migrated 400 workloads"}],
                claims=[
                    {
                        "sentence": "We migrated 400 workloads for the Treasury",
                        "citation_token": pp_token,
                    }
                ],
            ),
            "quality-management": _answer(
                f"## Quality Management\n\nOur QMS is audited annually [{bp_token}].",
                citations=[{"token": bp_token, "quote": "audited annually"}],
                claims=[{"sentence": "Our QMS is audited annually", "citation_token": bp_token}],
            ),
            "past-performance": _answer(
                f"## Past Performance\n\nTreasury cloud migration, prime [{pp_token}].",
                citations=[{"token": pp_token, "quote": "US Treasury"}],
                claims=[
                    {
                        "sentence": "Treasury cloud migration, prime",
                        "citation_token": pp_token,
                    }
                ],
            ),
        }
    )
    _run_id, result = await _run(database, ctx, llm)
    assert result.status == "done", result.error
    out = DraftStepOutput.model_validate(result.outputs["draft"])

    assert out.mode == "inline"
    assert [v.volume for v in out.volumes] == [
        "Volume I - Technical",
        "Volume II - Past Performance",
    ]
    assert out.sections == 3 and out.tasks == 0
    assert out.unresolved_citations == 0 and out.unsupported_claims == 0
    technical = out.volumes[0].sections[0]
    assert technical.section_id == "technical-approach" and technical.version == 1
    assert technical.requirements == ["R-001"] and technical.citations == 1
    assert technical.retrieved_chunks > 0

    # three Sonnet-class calls, one per section, each with its own untrusted requirements
    assert len(llm.calls) == 3
    assert {c.model for c in llm.calls} == {SETTINGS.llm_model_sonnet_class}
    assert {c.schema for c in llm.calls} == {"SectionDraft"}
    by_section = {
        next(
            s
            for s in ("technical-approach", "quality-management", "past-performance")
            if f"(id {s})" in call.user_text
        ): call
        for call in llm.calls
    }
    tech_call = by_section["technical-approach"]
    assert tech_call.system.startswith(UNTRUSTED_PREAMBLE)
    block = tech_call.cache_blocks[0].text
    assert block.startswith("<untrusted source=") and "R-001 [shall] (page 3)" in block
    assert "R-002" not in block  # only this section's requirements
    assert "Zero-downtime migration" in tech_call.user_text  # the win themes travel along
    assert "[KB:" in tech_call.user_text  # retrieved passages carry their tokens

    async with database.session(ctx["tenant_id"]) as session:
        drafts = (await session.execute(select(Draft).order_by(Draft.section_id))).scalars().all()
        assert [d.section_id for d in drafts] == [
            "past-performance",
            "quality-management",
            "technical-approach",
        ]
        assert all(d.current_version_id is not None and d.status == "draft" for d in drafts)
        assert {d.volume for d in drafts} == {
            "Volume I - Technical",
            "Volume II - Past Performance",
        }
        versions = (await session.execute(select(DraftVersion))).scalars().all()
        assert len(versions) == 3
        tech = next(v for v in versions if "Technical Approach" in v.body_html)
        assert tech.author == "agent" and tech.version == 1
        assert tech.body_html.startswith("<h2>") and "<script" not in tech.body_html
        assert "We migrated 400 workloads" in tech.body_text
        assert tech.citations[0]["token"] == pp_token
        assert tech.citations[0]["source_type"] == "past_performance"
        assert tech.citations[0]["source_id"] == str(ctx["past_performance"])
        assert tech.needs_input == [] and tech.flags == {}
        assert tech.model == SETTINGS.llm_model_sonnet_class
        assert (await session.execute(select(Task))).scalars().all() == []


async def test_unresolved_citations_and_uncited_claims_become_needs_input_and_tasks(
    database: Database, fake_embeddings: Any
) -> None:
    ctx = await _setup(
        database,
        fake_embeddings,
        volumes=[
            {
                "name": "Volume I - Technical",
                "sections": [
                    {
                        "id": "technical-approach",
                        "title": "Technical Approach",
                        "maps_requirements": ["R-001"],
                    }
                ],
            }
        ],
    )
    ghost = profile_token("certification", uuid.uuid4())  # a record this tenant does not have
    llm = ScriptedLLM(
        answers={
            "technical-approach": {
                "body_markdown": (
                    f"## Technical Approach\n\nWe are CMMI Level 5 appraised [{ghost}]. "
                    "We employ 250 cleared engineers."
                ),
                "citations": [{"token": ghost, "quote": "CMMI Level 5"}],
                "needs_input": [
                    {
                        "placeholder": "[NEEDS INPUT: transition schedule]",
                        "question": "How many weeks does transition take?",
                    }
                ],
                "claims": [
                    {"sentence": "We are CMMI Level 5 appraised", "citation_token": ghost},
                    {
                        "sentence": "We employ 250 cleared engineers.",
                        "citation_token": None,
                    },
                ],
            }
        }
    )
    _run_id, result = await _run(database, ctx, llm)
    assert result.status == "done", result.error
    out = DraftStepOutput.model_validate(result.outputs["draft"])
    section = out.volumes[0].sections[0]
    assert section.unresolved_citations == [ghost]
    assert section.unsupported_claims == 2  # both claims lost their (invented) citation
    assert section.needs_input == 4  # 1 from the model + 1 unresolved token + 2 claims
    assert section.tasks == 4
    assert out.tasks == 4

    async with database.session(ctx["tenant_id"]) as session:
        version = (await session.execute(select(DraftVersion))).scalar_one()
        assert ghost not in version.body_html
        assert NEEDS_INPUT_EVIDENCE in version.body_text
        assert version.citations == []  # the invented citation is never stored as a source
        assert len(version.needs_input) == 4
        assert all(item.get("task_id") for item in version.needs_input)
        tasks = (await session.execute(select(Task).order_by(Task.created_at))).scalars().all()
        assert len(tasks) == 4
        assert all(t.status == "open" and t.source == "agent" for t in tasks)
        assert all(t.ref["section_id"] == "technical-approach" for t in tasks)
        assert any("transition" in t.title for t in tasks)
        assert any("250 cleared engineers" in t.title for t in tasks)
        assert {str(t.id) for t in tasks} == {i["task_id"] for i in version.needs_input}


async def test_rag_only_sees_the_pursuit_tenants_chunks(
    database: Database, fake_embeddings: Any
) -> None:
    ctx = await _setup(database, fake_embeddings)
    other = await _setup(database, fake_embeddings)
    pp_token = profile_token("past_performance", ctx["past_performance"])
    llm = ScriptedLLM(
        default_json=_answer(
            f"## Section\n\nWe migrated 400 workloads [{pp_token}].",
            citations=[{"token": pp_token, "quote": "Migrated 400 workloads"}],
            claims=[{"sentence": "We migrated 400 workloads", "citation_token": pp_token}],
        )
    )
    await _run(database, ctx, llm)
    async with database.owner_session() as session:
        foreign = {
            str(row.source_id)
            for row in (
                await session.execute(
                    select(KBChunk).where(KBChunk.tenant_id == other["tenant_id"])
                )
            )
            .scalars()
            .all()
        }
    assert foreign, "the other tenant has its own chunks"
    retrieved = "\n".join(call.user_text for call in llm.calls)
    assert not [sid for sid in foreign if sid in retrieved]
    assert str(ctx["past_performance"]) in retrieved


async def test_a_second_run_appends_a_version_and_keeps_the_first(
    database: Database, fake_embeddings: Any
) -> None:
    ctx = await _setup(
        database,
        fake_embeddings,
        volumes=[
            {
                "name": "Volume I - Technical",
                "sections": [
                    {
                        "id": "technical-approach",
                        "title": "Technical Approach",
                        "maps_requirements": ["R-001"],
                    }
                ],
            }
        ],
    )
    pp_token = profile_token("past_performance", ctx["past_performance"])
    llm = ScriptedLLM(
        default_json=_answer(
            f"## First\n\nWe migrated 400 workloads [{pp_token}].",
            citations=[{"token": pp_token, "quote": "Migrated"}],
            claims=[{"sentence": "We migrated 400 workloads", "citation_token": pp_token}],
        )
    )
    await _run(database, ctx, llm)
    llm.default_json = _answer(
        f"## Second\n\nWe migrated 400 workloads [{pp_token}].",
        citations=[{"token": pp_token, "quote": "Migrated"}],
        claims=[{"sentence": "We migrated 400 workloads", "citation_token": pp_token}],
    )
    _run_id, result = await _run(database, ctx, llm)
    out = DraftStepOutput.model_validate(result.outputs["draft"])
    assert out.volumes[0].sections[0].version == 2
    async with database.session(ctx["tenant_id"]) as session:
        draft = (await session.execute(select(Draft))).scalar_one()
        rows = (
            (await session.execute(select(DraftVersion).order_by(DraftVersion.version)))
            .scalars()
            .all()
        )
        assert [r.version for r in rows] == [1, 2]
        assert "First" in rows[0].body_html and "Second" in rows[1].body_html
        assert draft.current_version_id == rows[1].id


async def test_drafting_without_an_outline_fails_the_step(
    database: Database, fake_embeddings: Any, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, fake_embeddings)
    async with database.owner_session() as session:
        await session.execute(
            PursuitArtifact.__table__.delete().where(
                PursuitArtifact.pursuit_id == ctx["pursuit_id"]
            )
        )
    _run_id, result = await _run(database, ctx, fake_llm)
    assert result.status == "failed"
    assert result.error is not None and "run the outline agent first" in result.error


async def test_estimate_prices_every_outline_section(
    database: Database, fake_embeddings: Any
) -> None:
    ctx = await _setup(database, fake_embeddings)
    async with database.session(ctx["tenant_id"]) as session:
        run = AgentRun(tenant_id=ctx["tenant_id"], kind="pipeline", pursuit_id=ctx["pursuit_id"])
        session.add(run)
        await session.flush()
        estimate = await estimate_draft(
            GuardContext(
                session=session,
                run=run,
                tenant_id=ctx["tenant_id"],
                outputs={},
                params={},
                services=_services(),
            )
        )
    assert estimate is not None
    assert estimate.model == SETTINGS.llm_model_sonnet_class
    assert estimate.output_tokens == 3 * 1800


async def test_celery_fanout_sends_one_task_per_volume(
    database: Database, fake_embeddings: Any, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _setup(database, fake_embeddings)
    sent: list[list[Any]] = []

    class _Group:
        def get(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return [{"volume": args[2], "sections": []} for args in sent]

    def _enqueue(args_list: list[list[Any]]) -> Any:
        sent.extend(args_list)
        return _Group()

    monkeypatch.setattr("app.agents.drafters.enqueue_group", _enqueue)
    services = _services(agent_fanout="celery", celery_task_always_eager=False)
    _run_id, result = await _run(database, ctx, fake_llm, services=services)
    assert result.status == "done", result.error
    out = DraftStepOutput.model_validate(result.outputs["draft"])
    assert out.mode == "celery"
    assert [args[2] for args in sent] == [
        "Volume I - Technical",
        "Volume II - Past Performance",
    ]
    assert {args[0] for args in sent} == {str(ctx["tenant_id"])}
    assert not fake_llm.calls  # the volumes are drafted by the workers, not here
