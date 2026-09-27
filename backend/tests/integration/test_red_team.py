"""M5-10: agent 8 -- the red-team reviewer, its single auto-revision, the review comments
the remaining issues become, and Gate 2 (POST /pursuits/{id}/approve-package)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from app.agents import pipeline
from app.agents.prompting import UNTRUSTED_PREAMBLE
from app.agents.red_team import RedTeamOutput
from app.agents.runner import AgentRunner
from app.agents.services import AgentServices
from app.core.citations import profile_token
from app.core.compliance import ARTIFACT_FORMAT_RULES, ARTIFACT_OUTLINE, ARTIFACT_RED_TEAM
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import PerformanceRole
from app.core.roles import Role
from app.models import (
    AuditLog,
    Comment,
    CompanyProfile,
    ComplianceItem,
    Draft,
    DraftVersion,
    Opportunity,
    OpportunityDocument,
    PastPerformance,
    Pursuit,
    PursuitArtifact,
    Requirement,
)
from app.services.drafts import save_version
from app.services.scanner import NoopScanner
from app.services.storage import StorageRouter
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]

REQUIREMENTS: list[tuple[str, str, str]] = [
    ("R-001", "The contractor shall migrate 400 workloads to a FedRAMP Moderate cloud.", "shall"),
    ("R-002", "Offerors shall describe their quality management system.", "shall"),
]
# a requirement the outline maps to no section: the reviewer must notice it is unanswered
UNMAPPED = ("R-003", "Offerors shall provide a transition plan.", "shall")

VOLUMES: list[dict[str, Any]] = [
    {
        "name": "Volume I - Technical",
        "sections": [
            {
                "id": "technical-approach",
                "title": "Technical Approach",
                "maps_requirements": ["R-001"],
                "evaluation_criterion": "Factor 1 - Technical",
                "page_budget": 2,
            },
            {
                "id": "quality-management",
                "title": "Quality Management",
                "maps_requirements": ["R-002"],
                "evaluation_criterion": "Factor 2 - Quality",
                "page_budget": 2,
            },
        ],
    }
]


def _services() -> AgentServices:
    return AgentServices(settings=SETTINGS, storage=StorageRouter(SETTINGS), scanner=NoopScanner())


def words(n: int, prefix: str = "filler") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


async def _setup(
    database: Database,
    *,
    volumes: list[dict[str, Any]] | None = None,
    long_technical: bool = False,
    unmapped_requirement: bool = False,
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Alpha Federal LLC"
        )
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
            scope="Migrated 400 workloads to a FedRAMP Moderate cloud",
        )
        doc = OpportunityDocument(
            opportunity_id=opp.id, url="https://x.test/rfp.pdf", file_name="rfp.pdf"
        )
        session.add_all([past, doc])
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
        items: dict[str, uuid.UUID] = {}
        rows = [*REQUIREMENTS, *([UNMAPPED] if unmapped_requirement else [])]
        for req_id, text, kind in rows:
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
            item = ComplianceItem(
                tenant_id=tenant.id,
                pursuit_id=pursuit.id,
                requirement_id=req.id,
                section="Technical Approach",
                reason="keyword",
            )
            session.add(item)
            await session.flush()
            items[req_id] = item.id
        session.add_all(
            [
                PursuitArtifact(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    kind=ARTIFACT_OUTLINE,
                    version=1,
                    data={
                        "outline": {
                            "volumes": volumes if volumes is not None else VOLUMES,
                            "win_themes": [],
                            "unmapped_requirements": [],
                        }
                    },
                ),
                PursuitArtifact(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    kind=ARTIFACT_FORMAT_RULES,
                    version=1,
                    data={"page_limit": 10, "font": "Times New Roman", "font_size_pt": 12},
                ),
            ]
        )
        await session.flush()
        ctx = {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "profile_id": profile.id,
            "pursuit_id": pursuit.id,
            "past_performance": past.id,
            "items": items,
        }

    token = profile_token("past_performance", ctx["past_performance"])
    ctx["token"] = token
    filler = f" {words(1600)}" if long_technical else ""
    async with database.session(ctx["tenant_id"]) as session:
        technical, _v = await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "technical-approach",
            title="Technical Approach",
            volume="Volume I - Technical",
            body_html=(
                f"<h2>Technical Approach</h2><p>We migrated 400 workloads for the "
                f"Treasury [{token}].{filler}</p>"
            ),
            citations=[{"token": token, "quote": "Migrated 400 workloads"}],
            author="agent",
            model="claude-sonnet-5",
        )
        quality, _qv = await save_version(
            session,
            ctx["tenant_id"],
            ctx["pursuit_id"],
            "quality-management",
            title="Quality Management",
            volume="Volume I - Technical",
            body_html="<h2>Quality Management</h2><p>We hold ISO 27001 certification.</p>",
            author="agent",
            model="claude-sonnet-5",
        )
        ctx["technical_draft"] = technical.id
        ctx["quality_draft"] = quality.id
    return ctx


def _review(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sections": [
            {
                "section_id": "technical-approach",
                "score": 78,
                "criterion_scores": [
                    {"criterion": "Factor 1 - Technical", "score": 78, "note": "solid"}
                ],
                "issues": [],
            },
            {
                "section_id": "quality-management",
                "score": 41,
                "criterion_scores": [
                    {"criterion": "Factor 2 - Quality", "score": 41, "note": "uncited"}
                ],
                "issues": [
                    {
                        "kind": "unsupported_claim",
                        "requirement_id": "R-002",
                        "sentence": "We hold ISO 27001 certification.",
                        "fix_suggestion": "Remove the certification claim or cite the record.",
                    },
                    {
                        "kind": "clarity",
                        "requirement_id": None,
                        "sentence": None,
                        "fix_suggestion": "Describe the QMS audit cycle.",
                    },
                ],
            },
        ],
        "overall_score": 60,
        "missing_requirements": [],
    }
    base.update(kwargs)
    return base


def _revision(body: str, addressed: list[int]) -> dict[str, Any]:
    return {"body_markdown": body, "addressed": addressed, "note": "revised"}


async def _run(
    database: Database, ctx: dict[str, Any], llm: Any, *, run_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, Any]:
    specs, finish = pipeline.plan_steps("red_team")
    runner = AgentRunner(database, tenant_id=ctx["tenant_id"], llm=llm, services=_services())
    if run_id is None:
        run_id = await runner.start(
            kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "red_team"}
        )
    return run_id, await runner.run(
        run_id,
        specs,
        finish_status=finish.status,
        finish_reason=finish.reason,
        finish_gate=finish.gate,
    )


async def _comments(database: Database, pursuit_id: uuid.UUID) -> list[Comment]:
    async with database.owner_session() as session:
        return list(
            (
                await session.execute(
                    select(Comment)
                    .where(Comment.pursuit_id == pursuit_id)
                    .order_by(Comment.created_at)
                )
            )
            .scalars()
            .all()
        )


# --- the step ------------------------------------------------------------------------------


def test_the_red_team_step_closes_gate_two() -> None:
    specs, finish = pipeline.plan_steps("all", gates_cleared=("gate1",))
    assert [s.agent for s in specs] == list(pipeline.PIPELINE_ORDER)
    assert finish.status == "paused" and finish.gate == pipeline.GATE_2
    assert "review" in (finish.reason or "")
    _specs, cleared = pipeline.plan_steps("all", gates_cleared=("gate1", "gate2"))
    assert cleared.status == "done" and cleared.gate is None


async def test_the_reviewer_scores_revises_once_and_stops_at_gate_two(
    database: Database,
) -> None:
    ctx = await _setup(database)
    llm = FakeLLM().queue(
        _review(),
        # the revision drops the uncited certification sentence and says so
        _revision(
            f"## Quality Management\n\nOur delivery teams audit the quality management "
            f"system annually [{ctx['token']}].",
            [0, 1],
        ),
    )
    _run_id, result = await _run(database, ctx, llm)
    assert result.status == "paused", result.error
    assert result.gate == pipeline.GATE_2
    out = RedTeamOutput.model_validate(result.outputs["red_team"])

    assert out.overall_score == 60
    assert out.revisions == 1 and out.resolved_issues == 2 and out.remaining_issues == 0
    scores = {s.section_id: s for s in out.sections}
    assert scores["technical-approach"].score == 78 and not scores["technical-approach"].revised
    quality = scores["quality-management"]
    assert quality.score == 41 and quality.revised and quality.issues == 2
    assert quality.version == 2 and quality.unsupported_before == 1
    assert quality.unsupported_after == 0, "the revision must leave no fabricated fact"

    # the review is Opus-class with the solicitation as data; the revision is Sonnet-class
    review_call, revision_call = llm.calls
    assert review_call.model == SETTINGS.llm_model_opus_class
    assert review_call.schema == "RedTeamReport"
    assert revision_call.model == SETTINGS.llm_model_sonnet_class
    assert revision_call.schema == "SectionRevision"
    assert UNTRUSTED_PREAMBLE in review_call.system
    block = review_call.cache_blocks[0].text
    assert block.startswith("<untrusted source=") and "R-001" in block
    assert "We hold ISO 27001 certification." in review_call.user_text
    assert "We hold ISO 27001 certification." not in block

    # the report is a versioned artifact and the run waits at Gate 2
    async with database.owner_session() as session:
        artifact = (
            await session.execute(
                select(PursuitArtifact).where(PursuitArtifact.kind == ARTIFACT_RED_TEAM)
            )
        ).scalar_one()
        assert artifact.version == 1 and out.version == 1
        assert artifact.data["report"]["sections"][1]["score"] == 41
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        assert pursuit is not None and pursuit.package_approved_at is None
    assert await _comments(database, ctx["pursuit_id"]) == []


async def test_issues_the_revision_did_not_close_become_review_comments(
    database: Database,
) -> None:
    ctx = await _setup(database)
    llm = FakeLLM().queue(
        _review(),
        # the revision keeps the uncited sentence and only claims the clarity fix
        _revision("## Quality Management\n\nWe hold ISO 27001 certification. Audited yearly.", [1]),
    )
    _run_id, result = await _run(database, ctx, llm)
    out = RedTeamOutput.model_validate(result.outputs["red_team"])
    assert out.revisions == 1 and out.remaining_issues == 1 and out.resolved_issues == 1

    comments = await _comments(database, ctx["pursuit_id"])
    assert len(comments) == 1
    assert comments[0].target_type == "draft"
    assert comments[0].target_id == ctx["quality_draft"]
    assert "Red team (unsupported_claim) on Quality Management" in comments[0].body
    assert "requirement R-002" in comments[0].body
    assert comments[0].author_user_id is None and comments[0].resolved_at is None
    # the section still shows the unsupported claim the revision kept
    assert out.sections[1].unsupported_after >= 1


async def test_a_measured_page_overrun_is_found_even_when_the_model_says_nothing(
    database: Database,
) -> None:
    ctx = await _setup(database, long_technical=True)
    llm = FakeLLM().queue(
        _review(),
        _revision(f"## Technical Approach\n\nWe migrated 400 workloads [{ctx['token']}].", [0]),
        _revision(f"## Quality Management\n\nAudited annually [{ctx['token']}].", [0, 1]),
    )
    _run_id, result = await _run(database, ctx, llm)
    out = RedTeamOutput.model_validate(result.outputs["red_team"])
    technical = next(s for s in out.sections if s.section_id == "technical-approach")
    assert technical.issues == 1 and technical.revised and technical.pages is not None
    assert technical.pages > 2 and technical.page_limit == 2
    report_issue = out.report.sections[0].issues[0]
    assert report_issue.kind == "page_limit"
    assert "against a 2-page limit" in report_issue.fix_suggestion
    # the revision is short enough, so the overrun is resolved rather than commented
    assert out.remaining_issues == 0 and await _comments(database, ctx["pursuit_id"]) == []


async def test_a_requirement_no_section_answers_becomes_a_comment_on_its_matrix_row(
    database: Database,
) -> None:
    ctx = await _setup(database, unmapped_requirement=True)
    llm = FakeLLM().queue(
        _review(missing_requirements=["R-003", "R-404"]),
        _revision(f"## Quality Management\n\nAudited annually [{ctx['token']}].", [0, 1]),
    )
    _run_id, result = await _run(database, ctx, llm)
    out = RedTeamOutput.model_validate(result.outputs["red_team"])
    assert out.missing_requirements == ["R-003"]
    assert any("never extracted" in w for w in out.warnings)

    comments = await _comments(database, ctx["pursuit_id"])
    assert len(comments) == 1
    assert comments[0].target_type == "compliance_item"
    assert comments[0].target_id == ctx["items"]["R-003"]
    assert "requirement R-003 is not answered" in comments[0].body


async def test_the_revision_may_not_cite_a_record_the_section_did_not_have(
    database: Database,
) -> None:
    ctx = await _setup(database)
    invented = profile_token("certification", uuid.uuid4())
    llm = FakeLLM().queue(
        _review(),
        _revision(f"## Quality Management\n\nWe are ISO certified [{invented}].", [0, 1]),
    )
    _run_id, result = await _run(database, ctx, llm)
    out = RedTeamOutput.model_validate(result.outputs["red_team"])
    assert any("did not have" in w and invented in w for w in out.warnings)

    async with database.owner_session() as session:
        version = (
            await session.execute(
                select(DraftVersion).where(
                    DraftVersion.draft_id == ctx["quality_draft"], DraftVersion.version == 2
                )
            )
        ).scalar_one()
    assert invented not in version.body_html
    assert "[NEEDS INPUT:" in version.body_text
    assert version.flags["red_team"]["revised"] is True
    assert version.flags["red_team"]["section_id"] == "quality-management"


async def test_a_section_is_auto_revised_exactly_once(database: Database) -> None:
    ctx = await _setup(database)
    first = FakeLLM().queue(
        _review(),
        _revision(f"## Quality Management\n\nAudited annually [{ctx['token']}].", [0, 1]),
    )
    await _run(database, ctx, first)

    second = FakeLLM().queue(_review())  # only a review: a second revision would raise
    _run_id, result = await _run(database, ctx, second)
    out = RedTeamOutput.model_validate(result.outputs["red_team"])
    assert out.revisions == 0
    assert any("already auto-revised once" in w for w in out.warnings)
    assert len(second.calls) == 1
    # the surviving findings are comments instead of a second rewrite
    assert out.remaining_issues == 2
    assert len(await _comments(database, ctx["pursuit_id"])) == 2
    async with database.owner_session() as session:
        versions = (
            await session.execute(
                select(DraftVersion).where(DraftVersion.draft_id == ctx["quality_draft"])
            )
        ).scalars()
        assert [v.version for v in versions] == [1, 2]
        artifacts = (
            await session.execute(
                select(PursuitArtifact).where(PursuitArtifact.kind == ARTIFACT_RED_TEAM)
            )
        ).scalars()
        assert sorted(a.version for a in artifacts) == [1, 2]


async def test_a_pursuit_with_no_drafts_reviews_nothing_and_calls_no_model(
    database: Database,
) -> None:
    ctx = await _setup(database, volumes=[{"name": "Volume I", "sections": []}])
    llm = FakeLLM()
    _run_id, result = await _run(database, ctx, llm)
    out = RedTeamOutput.model_validate(result.outputs["red_team"])
    assert llm.calls == []
    assert out.sections == [] and out.revisions == 0
    assert out.warnings == ["no section has a draft version to review"]
    assert out.version == 1


async def test_the_estimate_prices_a_review_plus_one_revision_per_section(
    database: Database,
) -> None:
    from app.agents.red_team import estimate_red_team
    from app.agents.runner import GuardContext
    from app.models import AgentRun

    ctx = await _setup(database)
    async with database.session(ctx["tenant_id"]) as session:
        run = AgentRun(tenant_id=ctx["tenant_id"], kind="pipeline", pursuit_id=ctx["pursuit_id"])
        session.add(run)
        await session.flush()
        estimate = await estimate_red_team(
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
    assert estimate.model == SETTINGS.llm_model_opus_class
    assert estimate.output_tokens == 3_000 + 2 * 2_000
    assert estimate.input_chars > 0


# --- Gate 2: POST /pursuits/{id}/approve-package ------------------------------------------------


def _headers(ctx: dict[str, Any], role: Role | None = None) -> dict[str, str]:
    if role is None:
        return auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    return auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=role)


async def test_approve_package_records_gate_two_and_approves_every_section(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/approve-package"

    # nothing to approve before the reviewer has run
    early = await api_client.post(url, json={}, headers=_headers(ctx))
    assert early.status_code == 409 and "red-team" in early.json()["detail"]

    llm = FakeLLM().queue(
        _review(),
        _revision(f"## Quality Management\n\nAudited annually [{ctx['token']}].", [0, 1]),
    )
    run_id, result = await _run(database, ctx, llm)
    assert result.status == "paused" and result.gate == "gate2"

    assert (
        await api_client.post(url, json={}, headers=_headers(ctx, Role.WRITER))
    ).status_code == 403
    assert (
        await api_client.post(url, json={}, headers=_headers(ctx, Role.REVIEWER))
    ).status_code == 403

    approved = await api_client.post(
        url, json={"note": "reviewed with capture", "inline": True}, headers=_headers(ctx)
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["approved_sections"] == 2 and body["previous_stage"] == "drafting"
    assert body["pursuit"]["stage"] == "final_approval"
    assert body["pursuit"]["package_approved_by"] == str(ctx["user_id"])
    assert body["pursuit"]["package_approved_at"] is not None
    assert body["pursuit"]["drafts"]["approved"] == 2
    assert body["resumed_run_id"] == str(run_id) and body["mode"] == "inline"
    # the resumed run plans past Gate 2 and finishes: the red-team step is already done
    assert body["result"]["status"] == "done"

    async with database.owner_session() as session:
        drafts = (
            await session.execute(select(Draft).where(Draft.pursuit_id == ctx["pursuit_id"]))
        ).scalars()
        assert all(d.status == "approved" and d.approved_by == ctx["user_id"] for d in drafts)
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "pursuit.package_approved")
            )
        ).scalars()
        rows = list(audits)
        assert len(rows) == 1
        assert rows[0].meta["approved_sections"] == 2
        assert rows[0].meta["red_team_version"] == 1
        assert rows[0].meta["note"] == "reviewed with capture"


async def test_gate_two_is_cleared_for_the_next_plan(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    from app.services.pursuits import cleared_gates

    ctx = await _setup(database)
    llm = FakeLLM().queue(
        _review(),
        _revision(f"## Quality Management\n\nAudited annually [{ctx['token']}].", [0, 1]),
    )
    await _run(database, ctx, llm)
    await api_client.post(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/approve-package",
        json={"inline": True},
        headers=_headers(ctx),
    )
    async with database.owner_session() as session:
        pursuit = await session.get(Pursuit, ctx["pursuit_id"])
        assert pursuit is not None
        assert cleared_gates(pursuit) == ("gate1", "gate2")


async def test_another_tenant_cannot_approve_the_package(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    async with database.owner_session() as session:
        other, other_user, _ = await create_tenant_with_owner(session)
    foreign = auth_headers(user_id=other_user.id, tenant_id=other.id, email=other_user.email)
    answer = await api_client.post(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/approve-package", json={}, headers=foreign
    )
    assert answer.status_code == 404
