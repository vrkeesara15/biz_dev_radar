"""M5-05: the matrix step assigns every requirement to a proposal section, extracts the
format rules, builds the region's submission checklist and exposes all three on
GET /api/v1/pursuits/{id}/matrix."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
from app.agents import pipeline
from app.agents.matrix import MatrixOutput, estimate_matrix
from app.agents.prompting import UNTRUSTED_PREAMBLE
from app.agents.runner import AgentRunner, GuardContext
from app.agents.services import AgentServices
from app.core.compliance import ARTIFACT_CHECKLIST, ARTIFACT_FORMAT_RULES
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import (
    AgentRun,
    AgentStep,
    CompanyProfile,
    ComplianceItem,
    Opportunity,
    OpportunityDocument,
    Pursuit,
    PursuitArtifact,
    Requirement,
)
from app.services.scanner import NoopScanner
from app.services.storage import StorageRouter
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]

# (req_id, text, type, volume) -- the IRS sources-sought shape plus two ambiguous clauses
US_REQUIREMENTS: list[tuple[str, str, str, str | None]] = [
    ("R-001", "Respondents must be registered and active in SAM.gov.", "eligibility", None),
    (
        "R-002",
        "The contractor shall migrate 400 workloads to a FedRAMP Moderate cloud.",
        "shall",
        None,
    ),
    (
        "R-003",
        "All personnel with access to Federal Tax Information complete Publication 1075 "
        "background investigations.",
        "must",
        None,
    ),
    ("R-004", "The contractor shall comply with clause H.7 in full.", "shall", None),
    ("R-005", "Factors are of approximately equal importance.", "evaluation", None),
    ("R-006", "Responses shall not exceed 10 pages, excluding the cover page.", "format", None),
    (
        "R-007",
        "Use 12-point Times New Roman font with one-inch margins.",
        "format",
        None,
    ),
    (
        "R-008",
        "File names shall follow the pattern CompanyName_IRS_SS_0042.pdf and be a single PDF file.",
        "format",
        None,
    ),
    (
        "R-009",
        "Responses must be emailed to market.research@irs.example.gov and the offer uploaded "
        "in SAM.gov.",
        "submission",
        None,
    ),
    ("R-010", "Describe the corporate quality programme.", "should", "Past Performance"),
]

ANSWER: dict[str, Any] = {
    "assignments": [
        {"req_id": "R-004", "section": "Management Plan"},
        {"req_id": "R-005", "section": "Technical Approach"},
    ]
}


def _services() -> AgentServices:
    return AgentServices(settings=SETTINGS, storage=StorageRouter(SETTINGS), scanner=NoopScanner())


async def _setup(
    database: Database,
    *,
    region: Region = Region.US,
    notice_type: NoticeType = NoticeType.SOURCES_SOUGHT,
    source_id: str = "sam_opps",
    requirements: list[tuple[str, str, str, str | None]] | None = None,
    currency: str = "USD",
    emd: Decimal | None = None,
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=region, legal_name="Matrix LLC")
        opp = Opportunity(
            source_id=source_id,
            external_id=f"ext-{uuid.uuid4().hex[:8]}",
            region=region,
            country="US" if region is Region.US else "IN",
            currency=currency,
            notice_type=notice_type,
            title="Matrix notice",
            source_url="https://portal.test/notice",
            set_aside="small_business" if region is Region.US else None,
            emd_amount=emd,
        )
        session.add_all([profile, opp])
        await session.flush()
        doc = OpportunityDocument(
            opportunity_id=opp.id, url="https://x.test/sow.pdf", file_name="sow.pdf"
        )
        session.add(doc)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id, profile_id=profile.id, opportunity_id=opp.id, created_by=user.id
        )
        session.add(pursuit)
        await session.flush()
        for req_id, text, kind, volume in requirements or []:
            session.add(
                Requirement(
                    tenant_id=tenant.id,
                    pursuit_id=pursuit.id,
                    req_id=req_id,
                    text=text,
                    document_id=doc.id,
                    page=1,
                    type=kind,
                    volume=volume,
                    quote=text,
                )
            )
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "profile_id": profile.id,
            "pursuit_id": pursuit.id,
            "opportunity_id": opp.id,
            "document_id": doc.id,
        }


async def _run(database: Database, ctx: dict[str, Any], llm: FakeLLM) -> Any:
    specs, finish = pipeline.plan_steps("matrix")
    assert [s.agent for s in specs] == ["matrix"] and finish.status == "done"
    runner = AgentRunner(database, tenant_id=ctx["tenant_id"], llm=llm, services=_services())
    run_id = await runner.start(
        kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "matrix"}
    )
    return run_id, await runner.run(run_id, specs)


async def test_matrix_maps_every_requirement_and_extracts_rules_and_checklist(
    database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    fake_llm.queue(ANSWER)
    run_id, result = await _run(database, ctx, fake_llm)
    assert result.status == "done", result.error
    out = MatrixOutput.model_validate(result.outputs["matrix"])

    assert out.requirements == 10 and len(out.items) == 10
    by_req = {i.req_id: i for i in out.items}
    assert (by_req["R-001"].section, by_req["R-001"].reason) == (
        "Eligibility and Certifications",
        "type",
    )
    assert (by_req["R-002"].section, by_req["R-002"].reason) == ("Technical Approach", "keyword")
    assert (by_req["R-003"].section, by_req["R-003"].reason) == (
        "Staffing and Key Personnel",
        "keyword",
    )
    assert (by_req["R-006"].section, by_req["R-006"].reason) == ("Submission Package", "type")
    assert (by_req["R-010"].section, by_req["R-010"].reason) == ("Past Performance", "volume")
    # only the two clauses the heuristics could not place went to the model
    assert (by_req["R-004"].section, by_req["R-004"].reason) == ("Management Plan", "model")
    assert (by_req["R-005"].section, by_req["R-005"].reason) == ("Technical Approach", "model")
    assert out.classified == 2
    assert all(i.status == "open" for i in out.items)
    assert sum(out.sections.values()) == 10

    # the one classification call: haiku class, untrusted framing, only the ambiguous ones
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call.model == SETTINGS.llm_model_haiku_class and call.schema == "ClassificationOutput"
    assert call.system.startswith(UNTRUSTED_PREAMBLE)
    block = call.cache_blocks[0].text
    assert "R-004" in block and "R-005" in block and "R-001" not in block
    assert call.kwargs["temperature"] == 0.0

    rules = out.format_rules
    assert rules.page_limit == 10 and rules.font == "Times New Roman" and rules.font_size_pt == 12
    assert rules.margins == "one-inch margins" and rules.file_types == ["PDF"]
    assert rules.file_naming == "CompanyName_IRS_SS_0042.pdf"
    assert rules.portal == "SAM.gov" and rules.submission_method == "email"
    assert rules.email == "market.research@irs.example.gov"
    assert rules.sources["page_limit"] == "R-006" and rules.sources["portal"] == "R-009"

    checklist = {i.key: i for i in out.checklist}
    assert checklist["sam_registration"].required and checklist["reps_certs"].required
    assert checklist["sam_registration"].source_req_ids == ["R-001", "R-009"]
    assert checklist["capability_statement"].required  # sources sought
    assert checklist["set_aside_eligibility"].required  # the notice is a set-aside

    async with database.session(ctx["tenant_id"]) as session:
        rows = (
            await session.execute(
                select(ComplianceItem, Requirement)
                .join(Requirement, Requirement.id == ComplianceItem.requirement_id)
                .order_by(Requirement.req_id)
            )
        ).all()
        assert len(rows) == 10
        assert all(item.status == "open" and item.owner_user_id is None for item, _ in rows)
        assert {req.req_id for _, req in rows} == {r[0] for r in US_REQUIREMENTS}
        artifacts = (
            (await session.execute(select(PursuitArtifact).order_by(PursuitArtifact.kind)))
            .scalars()
            .all()
        )
        assert [(a.kind, a.version, a.created_by) for a in artifacts] == [
            (ARTIFACT_CHECKLIST, 1, "agent"),
            (ARTIFACT_FORMAT_RULES, 1, "agent"),
        ]
        step = (
            await session.execute(select(AgentStep).where(AgentStep.run_id == run_id))
        ).scalar_one()
        assert step.model == SETTINGS.llm_model_haiku_class
        assert step.input_ref == f"pursuit:{ctx['pursuit_id']}:requirements=10"

    # a second run replaces the rows and appends version 2 of each artifact
    fake_llm.queue(ANSWER)
    _, again = await _run(database, ctx, fake_llm)
    assert again.status == "done"
    async with database.session(ctx["tenant_id"]) as session:
        assert len((await session.execute(select(ComplianceItem))).scalars().all()) == 10
        versions = sorted(
            (a.kind, a.version)
            for a in (await session.execute(select(PursuitArtifact))).scalars().all()
        )
        assert versions == [
            (ARTIFACT_CHECKLIST, 1),
            (ARTIFACT_CHECKLIST, 2),
            (ARTIFACT_FORMAT_RULES, 1),
            (ARTIFACT_FORMAT_RULES, 2),
        ]


async def test_india_matrix_checklist_and_no_llm_call_when_nothing_is_ambiguous(
    database: Database, fake_llm: FakeLLM
) -> None:
    reqs: list[tuple[str, str, str, str | None]] = [
        (
            "R-001",
            "Bidders must furnish EMD of INR 2,50,000 by bank guarantee.",
            "eligibility",
            None,
        ),
        (
            "R-002",
            "Average annual turnover of INR 5 crore in the last three years.",
            "eligibility",
            None,
        ),
        (
            "R-003",
            "Technical and financial covers must be signed with a Class 3 DSC.",
            "submission",
            None,
        ),
        ("R-004", "Provide a transition plan and monthly reporting.", "shall", None),
    ]
    ctx = await _setup(
        database,
        region=Region.IN,
        notice_type=NoticeType.GEM_BID,
        source_id="gem_bids",
        currency="INR",
        emd=Decimal("250000"),
        requirements=reqs,
    )
    _, result = await _run(database, ctx, fake_llm)
    assert result.status == "done" and fake_llm.calls == []  # nothing ambiguous: nothing spent
    out = MatrixOutput.model_validate(result.outputs["matrix"])
    assert out.classified == 0
    checklist = {i.key: i for i in out.checklist}
    assert checklist["emd"].required and checklist["emd"].source_req_ids == ["R-001"]
    assert checklist["bank_guarantee"].required
    assert checklist["dsc"].required and checklist["dsc_signed_covers"].required
    assert checklist["turnover_certificate"].source_req_ids == ["R-002"]
    assert checklist["affidavit"].required and checklist["gem_seller"].required
    assert "₹2,50,000" in (checklist["emd"].note or "")
    assert set(out.sections) == {
        "Eligibility and Certifications",
        "Submission Package",
        "Management Plan",
    }
    # the portal falls back to the opportunity's source url when no requirement names one
    assert out.format_rules.portal == "https://portal.test/notice"

    # the estimator prices only the ambiguous requirements; there are none here
    async with database.session(ctx["tenant_id"]) as session:
        run = (await session.execute(select(AgentRun))).scalar_one()
        assert (
            await estimate_matrix(
                GuardContext(
                    session=session,
                    run=run,
                    tenant_id=ctx["tenant_id"],
                    outputs={},
                    params={},
                    services=_services(),
                )
            )
            is None
        )


async def test_estimate_matrix_prices_only_the_ambiguous_requirements(
    database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    fake_llm.queue(ANSWER)
    run_id, _ = await _run(database, ctx, fake_llm)
    async with database.session(ctx["tenant_id"]) as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        estimate = await estimate_matrix(
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
    assert estimate.model == SETTINGS.llm_model_haiku_class
    ambiguous_chars = len(US_REQUIREMENTS[3][1]) + len(US_REQUIREMENTS[4][1])
    assert estimate.input_chars > ambiguous_chars
    assert estimate.output_tokens == 80


async def test_matrix_api_returns_items_rules_and_checklist(
    app: Any, api_client: httpx.AsyncClient, database: Database, fake_llm: FakeLLM
) -> None:
    ctx = await _setup(database, requirements=US_REQUIREMENTS)
    viewer = auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=Role.VIEWER)
    url = f"/api/v1/pursuits/{ctx['pursuit_id']}/matrix"

    # before the agent runs the matrix is empty, not an error
    empty = await api_client.get(url, headers=viewer)
    assert empty.status_code == 200
    body = empty.json()
    assert body["items"] == [] and body["format_rules"] is None and body["checklist"] == []
    assert body["generated_at"] is None and body["checklist_version"] is None

    fake_llm.queue(ANSWER)
    await _run(database, ctx, fake_llm)

    resp = await api_client.get(url, headers=viewer)
    assert resp.status_code == 200
    body = resp.json()
    assert body["pursuit_id"] == str(ctx["pursuit_id"])
    assert [i["req_id"] for i in body["items"]] == [r[0] for r in US_REQUIREMENTS]
    first = body["items"][0]
    assert first["section"] == "Eligibility and Certifications" and first["reason"] == "type"
    assert first["document_id"] == str(ctx["document_id"]) and first["page"] == 1
    assert first["quote"] and first["status"] == "open" and first["owner_user_id"] is None
    assert body["format_rules"]["page_limit"] == 10
    assert body["format_rules"]["font"] == "Times New Roman"
    assert body["format_rules_version"] == 1 and body["checklist_version"] == 1
    assert {i["key"] for i in body["checklist"]} >= {"sam_registration", "reps_certs", "sf_33"}
    assert body["generated_at"] is not None

    # another tenant cannot read it
    async with database.owner_session() as session:
        tenant_b, user_b, _ = await create_tenant_with_owner(session)
    other = auth_headers(user_id=user_b.id, tenant_id=tenant_b.id)
    assert (await api_client.get(url, headers=other)).status_code == 404
