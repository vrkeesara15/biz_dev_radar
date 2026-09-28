"""M5-07: agent 5 builds the proposal outline and the win themes from the matrix."""

from __future__ import annotations

import uuid
from typing import Any

from app.agents import pipeline
from app.agents.outline import OutlineOutput, estimate_outline, load_inputs
from app.agents.prompting import UNTRUSTED_PREAMBLE
from app.agents.runner import AgentRunner, GuardContext
from app.agents.services import AgentServices
from app.core.citations import profile_token
from app.core.compliance import ARTIFACT_FORMAT_RULES, ARTIFACT_OUTLINE
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import BoilerplateKind, CertificationKind, PerformanceRole
from app.models import (
    AgentRun,
    BoilerplateBlock,
    Certification,
    CompanyProfile,
    ComplianceItem,
    Opportunity,
    OpportunityDocument,
    PastPerformance,
    Pursuit,
    PursuitArtifact,
    Requirement,
    ServiceLine,
)
from app.services.evidence import load_evidence
from app.services.scanner import NoopScanner
from app.services.storage import StorageRouter
from sqlalchemy import select

from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]

US_REQUIREMENTS: list[tuple[str, str, str, str]] = [
    (
        "R-001",
        "Volume I shall describe the technical approach per Section L.3.",
        "shall",
        "Technical Approach",
    ),
    (
        "R-002",
        "Offerors shall provide three past performance references.",
        "shall",
        "Past Performance",
    ),
    (
        "R-003",
        "Section M: technical merit is significantly more important than price.",
        "evaluation",
        "Technical Approach",
    ),
]

IN_REQUIREMENTS: list[tuple[str, str, str, str]] = [
    (
        "R-001",
        "The technical cover shall contain the filled Annexure A.",
        "shall",
        "Submission Package",
    ),
    (
        "R-002",
        "The financial cover shall contain the BOQ in the prescribed format.",
        "shall",
        "Price",
    ),
]


def _services() -> AgentServices:
    return AgentServices(settings=SETTINGS, storage=StorageRouter(SETTINGS), scanner=NoopScanner())


async def _setup(
    database: Database,
    *,
    region: Region = Region.US,
    requirements: list[tuple[str, str, str, str]] | None = None,
    page_limit: int | None = 10,
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=region, legal_name="Alpha Federal LLC")
        opp = Opportunity(
            source_id="sam_opps" if region is Region.US else "cppp",
            external_id=f"ext-{uuid.uuid4().hex[:8]}",
            region=region,
            country="US" if region is Region.US else "IN",
            currency="USD" if region is Region.US else "INR",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
            source_url="https://portal.test/notice",
        )
        session.add_all([profile, opp])
        await session.flush()
        past = PastPerformance(
            tenant_id=tenant.id,
            profile_id=profile.id,
            title="Treasury cloud migration",
            customer="US Treasury",
            role=PerformanceRole.PRIME,
            scope="Migrated 400 workloads with zero downtime",
        )
        cert = Certification(
            tenant_id=tenant.id,
            profile_id=profile.id,
            kind=CertificationKind.ISO_9001,
            cert_number="ISO-9001-42",
        )
        line = ServiceLine(
            tenant_id=tenant.id,
            profile_id=profile.id,
            name="Cloud modernisation",
            description="Lift-and-shift and refactor programmes for federal agencies",
        )
        block = BoilerplateBlock(
            tenant_id=tenant.id,
            profile_id=profile.id,
            kind=BoilerplateKind.COMPANY_OVERVIEW,
            title="Company overview",
            body="<p>Alpha Federal LLC was founded in 2015.</p>",
        )
        doc = OpportunityDocument(
            opportunity_id=opp.id, url="https://x.test/rfp.pdf", file_name="rfp.pdf"
        )
        session.add_all([past, cert, line, block, doc])
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
        for req_id, text, kind, section in requirements or []:
            req = Requirement(
                tenant_id=tenant.id,
                pursuit_id=pursuit.id,
                req_id=req_id,
                text=text,
                document_id=doc.id,
                page=1,
                type=kind,
                quote=text,
            )
            session.add(req)
            await session.flush()
            session.add(
                ComplianceItem(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    requirement_id=req.id,
                    section=section,
                    reason="keyword",
                )
            )
        if page_limit is not None:
            session.add(
                PursuitArtifact(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    kind=ARTIFACT_FORMAT_RULES,
                    version=1,
                    data={"page_limit": page_limit, "file_types": ["PDF"], "sources": {}},
                )
            )
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "profile_id": profile.id,
            "pursuit_id": pursuit.id,
            "past_performance": past.id,
            "certification": cert.id,
            "service_line": line.id,
            "boilerplate": block.id,
        }


async def _run(database: Database, ctx: dict[str, Any], llm: FakeLLM) -> Any:
    specs, finish = pipeline.plan_steps("outline")
    assert [s.agent for s in specs] == ["outline"] and finish.status == "done"
    runner = AgentRunner(database, tenant_id=ctx["tenant_id"], llm=llm, services=_services())
    run_id = await runner.start(
        kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "outline"}
    )
    return run_id, await runner.run(run_id, specs)


def _answer(ctx: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    pp = profile_token("past_performance", ctx["past_performance"])
    cert = profile_token("certification", ctx["certification"])
    line = profile_token("service_line", ctx["service_line"])
    answer: dict[str, Any] = {
        "volumes": [
            {
                "name": "Volume I - Technical",
                "sections": [
                    {
                        "id": "technical-approach",
                        "title": "Technical Approach",
                        "maps_requirements": ["R-001", "R-003"],
                        "evaluation_criterion": "Factor 1 - Technical merit",
                        "page_budget": 6,
                    }
                ],
            },
            {
                "name": "Volume II - Past Performance",
                "sections": [
                    {
                        "id": "past-performance",
                        "title": "Past Performance",
                        "maps_requirements": ["R-002"],
                        "evaluation_criterion": "Factor 2 - Past performance",
                        "page_budget": 4,
                    }
                ],
            },
        ],
        "win_themes": [
            {
                "theme": "Zero-downtime migration",
                "discriminator": "We moved 400 Treasury workloads without an outage",
                "evidence_citations": [pp],
            },
            {
                "theme": "Audited quality system",
                "discriminator": "ISO 9001 certified delivery process",
                "evidence_citations": [cert],
            },
            {
                "theme": "Federal cloud specialists",
                "discriminator": "Cloud modernisation is our named service line",
                "evidence_citations": [line],
            },
        ],
        "unmapped_requirements": [],
    }
    answer.update(overrides)
    return answer


async def test_outline_maps_every_requirement_and_grounds_the_win_themes(
    database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    fake_llm.queue(_answer(ctx))
    _run_id, result = await _run(database, ctx, fake_llm)
    assert result.status == "done", result.error
    out = OutlineOutput.model_validate(result.outputs["outline"])

    assert out.warnings == []
    assert out.volumes == 2 and out.sections == 2
    assert out.requirements == 3 and out.mapped == 3 and out.unmapped == []
    assert out.page_limit == 10 and out.evidence_records == 4
    assert out.outline.section_ids() == ["technical-approach", "past-performance"]
    first = out.outline.volumes[0].sections[0]
    assert first.maps_requirements == ["R-001", "R-003"]
    assert first.evaluation_criterion == "Factor 1 - Technical merit"
    assert first.page_budget == 6
    assert len(out.outline.win_themes) == 3
    assert out.outline.win_themes[0].evidence_citations == [
        profile_token("past_performance", ctx["past_performance"])
    ]
    assert out.version == 1

    # Sonnet-class drafting model; requirements are data, evidence is trusted context
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call.model == SETTINGS.llm_model_sonnet_class and call.schema == "Outline"
    assert call.system.startswith(UNTRUSTED_PREAMBLE)
    assert "Section L" in call.system  # the US structure guidance
    block = call.cache_blocks[0].text
    assert block.startswith("<untrusted source=")
    assert "R-002 [shall] (Past Performance)" in block
    assert "Section L / Section M language" in call.user_text
    assert f"[{profile_token('certification', ctx['certification'])}]" in call.user_text
    assert "Treasury cloud migration" in call.user_text and "Treasury cloud migration" not in block

    async with database.session(ctx["tenant_id"]) as session:
        artifact = (
            await session.execute(
                select(PursuitArtifact).where(PursuitArtifact.kind == ARTIFACT_OUTLINE)
            )
        ).scalar_one()
        assert artifact.version == 1 and artifact.created_by == "agent"
        assert artifact.data["outline"]["volumes"][1]["name"] == "Volume II - Past Performance"


async def test_unmapped_requirements_and_bad_citations_become_warnings(
    database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    answer = _answer(ctx)
    answer["volumes"][1]["sections"][0]["maps_requirements"] = ["R-404"]  # R-002 left behind
    answer["win_themes"][1]["evidence_citations"] = [
        profile_token("certification", uuid.uuid4())  # a record this tenant does not have
    ]
    fake_llm.queue(answer)
    _run_id, result = await _run(database, ctx, fake_llm)
    out = OutlineOutput.model_validate(result.outputs["outline"])
    assert out.unmapped == ["R-002"] and out.mapped == 2
    assert out.unknown_requirements == ["R-404"]
    assert len(out.dropped_citations) == 1
    assert out.outline.unmapped_requirements == ["R-002"]
    assert any("not mapped to a section" in w for w in out.warnings)
    assert any("do not exist" in w for w in out.warnings)
    assert any("resolve to no profile record" in w for w in out.warnings)
    assert any("cite no profile record" in w for w in out.warnings)


async def test_india_outline_is_asked_for_covers_and_warns_without_them(
    database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, region=Region.IN, requirements=IN_REQUIREMENTS, page_limit=None)
    answer = _answer(ctx)
    answer["volumes"] = [
        {
            "name": "Technical Cover",
            "sections": [
                {"id": "annexure-a", "title": "Annexure A", "maps_requirements": ["R-001"]}
            ],
        },
        {
            "name": "Financial Cover",
            "sections": [
                {"id": "boq", "title": "Bill of quantities", "maps_requirements": ["R-002"]}
            ],
        },
    ]
    fake_llm.queue(answer)
    _run_id, result = await _run(database, ctx, fake_llm)
    out = OutlineOutput.model_validate(result.outputs["outline"])
    assert out.warnings == [] and out.page_limit is None
    call = fake_llm.calls[0]
    assert "Indian tender" in call.system and "Financial cover" in call.system
    assert "Section L" not in call.system

    # the same tender answered with US volumes is flagged
    ctx2 = await _setup(database, region=Region.IN, requirements=IN_REQUIREMENTS, page_limit=None)
    fake_llm.queue(_answer(ctx2))
    _run_id2, result2 = await _run(database, ctx2, fake_llm)
    out2 = OutlineOutput.model_validate(result2.outputs["outline"])
    assert any("no financial cover" in w for w in out2.warnings)


async def test_a_second_run_appends_a_version(database: Database, fake_llm: FakeLLM) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    fake_llm.queue(_answer(ctx), _answer(ctx))
    await _run(database, ctx, fake_llm)
    _run_id, result = await _run(database, ctx, fake_llm)
    out = OutlineOutput.model_validate(result.outputs["outline"])
    assert out.version == 2
    async with database.session(ctx["tenant_id"]) as session:
        versions = (
            (
                await session.execute(
                    select(PursuitArtifact.version)
                    .where(PursuitArtifact.kind == ARTIFACT_OUTLINE)
                    .order_by(PursuitArtifact.version)
                )
            )
            .scalars()
            .all()
        )
        assert list(versions) == [1, 2]


async def test_evidence_is_tenant_scoped_and_estimate_uses_the_drafting_model(
    database: Database,
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    other = await _setup(database, requirements=US_REQUIREMENTS)
    async with database.session(ctx["tenant_id"]) as session:
        records = await load_evidence(session, ctx["profile_id"])
        assert {r.source_type for r in records} == {
            "past_performance",
            "service_line",
            "certification",
            "boilerplate",
        }
        assert await load_evidence(session, other["profile_id"]) == []  # RLS hides tenant B
        inputs = await load_inputs(session, ctx["pursuit_id"])
        assert inputs.req_ids == ["R-001", "R-002", "R-003"]
        assert inputs.sections_by_req["R-002"] == "Past Performance"
        assert inputs.format_rules.page_limit == 10

        run = AgentRun(tenant_id=ctx["tenant_id"], kind="pipeline", pursuit_id=ctx["pursuit_id"])
        session.add(run)
        await session.flush()
        estimate = await estimate_outline(
            GuardContext(
                session=session,
                run=run,
                tenant_id=ctx["tenant_id"],
                outputs={},
                params={},
                services=_services(),
            )
        )
    assert estimate is not None and estimate.model == SETTINGS.llm_model_sonnet_class
    assert estimate.output_tokens == 3000
