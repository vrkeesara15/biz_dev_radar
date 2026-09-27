"""M1-12: kb_chunks indexing (files, boilerplate, past performance, service lines),
re-index on change only, similarity_search, API hook and opportunity embeddings."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from app.celery_app import INDEX_PROFILE_TASK, celery_app, index_profile_task
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType, OpportunityIn
from app.core.profile_fields import BoilerplateKind, PerformanceRole, ProfileFileKind
from app.core.roles import Role
from app.jobs.index_profile import index_profile_job
from app.models import (
    BoilerplateBlock,
    CompanyProfile,
    File,
    KBChunk,
    Opportunity,
    PastPerformance,
    ProfileFile,
    ServiceLine,
)
from app.services.embeddings import FakeEmbeddings
from app.services.events import EventBus
from app.services.ingest import ingest
from app.services.knowledge_base import (
    SourceType,
    boilerplate_text,
    index_profile,
    past_performance_text,
    service_line_text,
    similarity_search,
)
from app.services.opportunity_embeddings import (
    OpportunityEmbedder,
    embed_opportunity,
    install_opportunity_embeddings,
    opportunity_text,
)
from app.services.storage import StorageRouter
from sqlalchemy import func, select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"
TEXT_PDF = (FIXTURES / "text.pdf").read_bytes()
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class Seed:
    def __init__(self) -> None:
        self.tenant_id: uuid.UUID
        self.user_id: uuid.UUID
        self.profile_id: uuid.UUID
        self.boilerplate_id: uuid.UUID
        self.pp_id: uuid.UUID
        self.line_id: uuid.UUID
        self.profile_file_id: uuid.UUID


async def _seed(
    database: Database, settings: Settings, *, with_file: bool = True, region: Region = Region.US
) -> Seed:
    seed = Seed()
    router = StorageRouter(settings)
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, region=region, data_residency=region
        )
        profile = CompanyProfile(tenant_id=tenant.id, region=region, legal_name="Alpha Federal LLC")
        session.add(profile)
        await session.flush()
        boilerplate = BoilerplateBlock(
            tenant_id=tenant.id,
            profile_id=profile.id,
            kind=BoilerplateKind.COMPANY_OVERVIEW,
            title="Company overview",
            body="<p>Alpha Federal delivers <b>cloud migration</b> and FedRAMP hosting.</p>",
        )
        pp = PastPerformance(
            tenant_id=tenant.id,
            profile_id=profile.id,
            title="IRS data center consolidation",
            customer="Internal Revenue Service",
            role=PerformanceRole.PRIME,
            scope="Consolidated 12 data centers into two FedRAMP High cloud regions.",
            outcomes="Cut hosting cost 38 percent.",
            technologies=["AWS GovCloud", "Terraform"],
        )
        line = ServiceLine(
            tenant_id=tenant.id,
            profile_id=profile.id,
            name="Road paving",
            description="Asphalt paving and resurfacing for municipal roads.",
            differentiators=["Own asphalt plant"],
            tools=["Pavers"],
        )
        session.add_all([boilerplate, pp, line])
        await session.flush()
        seed.tenant_id, seed.user_id, seed.profile_id = tenant.id, user.id, profile.id
        seed.boilerplate_id, seed.pp_id, seed.line_id = boilerplate.id, pp.id, line.id
        if with_file:
            file_id = uuid.uuid4()
            key = f"tenants/{tenant.id}/files/{file_id}.pdf"
            await router.for_region(region).put(key, TEXT_PDF, "application/pdf")
            file = File(
                id=file_id,
                tenant_id=tenant.id,
                filename="capability.pdf",
                extension="pdf",
                kind="pdf",
                content_type="application/pdf",
                size_bytes=len(TEXT_PDF),
                sha256=hashlib.sha256(TEXT_PDF).hexdigest(),
                region=region,
                bucket="bidradar-us" if region is Region.US else "bidradar-in",
                key=key,
                uploaded_by=user.id,
            )
            session.add(file)
            await session.flush()
            profile_file = ProfileFile(
                tenant_id=tenant.id,
                profile_id=profile.id,
                file_id=file.id,
                kind=ProfileFileKind.CAPABILITY_STATEMENT,
                title="Capability statement",
            )
            session.add(profile_file)
            await session.flush()
            seed.profile_file_id = profile_file.id
    return seed


async def _chunks(database: Database, tenant_id: uuid.UUID, profile_id: uuid.UUID) -> list[KBChunk]:
    async with database.session(tenant_id) as session:
        rows = await session.execute(
            select(KBChunk)
            .where(KBChunk.profile_id == profile_id)
            .order_by(KBChunk.source_type, KBChunk.source_id, KBChunk.chunk_index)
        )
        return list(rows.scalars().all())


async def test_index_profile_chunks_every_source_type_with_pages(
    database: Database, settings: Settings, fake_embeddings: FakeEmbeddings
) -> None:
    seed = await _seed(database, settings)
    router = StorageRouter(settings)
    async with database.session(seed.tenant_id) as session:
        result = await index_profile(
            session, seed.profile_id, embeddings=fake_embeddings, storage=router
        )
    assert result.warnings == [] and result.removed == [] and result.unchanged == []
    assert sorted(t for t, _ in result.indexed) == [
        "boilerplate",
        "past_performance",
        "profile_file",
        "service_line",
    ]
    chunks = await _chunks(database, seed.tenant_id, seed.profile_id)
    assert len(chunks) == result.chunks >= 4
    by_type = {t: [c for c in chunks if c.source_type == t] for t in SourceType}
    file_chunks = by_type[SourceType.PROFILE_FILE]
    assert file_chunks and file_chunks[0].source_id == seed.profile_file_id
    assert file_chunks[0].page == 1 and "40 field offices" in file_chunks[0].text
    assert all(c.tenant_id == seed.tenant_id and len(c.embedding) == 1024 for c in chunks)
    bp = by_type[SourceType.BOILERPLATE][0]
    assert bp.source_id == seed.boilerplate_id and "cloud migration" in bp.text
    assert "<b>" not in bp.text and bp.page == 1
    pp = by_type[SourceType.PAST_PERFORMANCE][0]
    assert pp.source_id == seed.pp_id and "Internal Revenue Service" in pp.text
    assert "AWS GovCloud" in pp.text and "38 percent" in pp.text
    line = by_type[SourceType.SERVICE_LINE][0]
    assert line.source_id == seed.line_id and "Own asphalt plant" in line.text
    assert {c.content_hash for c in file_chunks} == {file_chunks[0].content_hash}
    # every chunk was embedded as a document, in batches per source
    assert all(kind == "document" for _, kind in fake_embeddings.calls)
    assert sum(len(t) for t, _ in fake_embeddings.calls) == len(chunks)


async def test_reindex_touches_only_changed_sources_and_removes_orphans(
    database: Database, settings: Settings, fake_embeddings: FakeEmbeddings
) -> None:
    seed = await _seed(database, settings)
    router = StorageRouter(settings)
    async with database.session(seed.tenant_id) as session:
        await index_profile(session, seed.profile_id, embeddings=fake_embeddings, storage=router)
    before = await _chunks(database, seed.tenant_id, seed.profile_id)
    fake_embeddings.calls.clear()
    # nothing changed: no embedding call, same rows
    async with database.session(seed.tenant_id) as session:
        again = await index_profile(
            session, seed.profile_id, embeddings=fake_embeddings, storage=router
        )
    assert again.indexed == [] and len(again.unchanged) == 4 and fake_embeddings.calls == []
    assert [c.id for c in await _chunks(database, seed.tenant_id, seed.profile_id)] == [
        c.id for c in before
    ]
    # change the boilerplate, delete the service line: one re-embed, one removal
    async with database.session(seed.tenant_id) as session:
        bp = await session.get(BoilerplateBlock, seed.boilerplate_id)
        assert bp is not None
        bp.body = "<p>Alpha Federal now also delivers zero-trust network modernisation.</p>"
        line = await session.get(ServiceLine, seed.line_id)
        assert line is not None
        await session.delete(line)
    async with database.session(seed.tenant_id) as session:
        changed = await index_profile(
            session, seed.profile_id, embeddings=fake_embeddings, storage=router
        )
    assert changed.indexed == [("boilerplate", seed.boilerplate_id)]
    assert changed.removed == [("service_line", seed.line_id)]
    assert len(changed.unchanged) == 2
    assert len(fake_embeddings.calls) == 1 and "zero-trust" in fake_embeddings.calls[0][0][0]
    after = await _chunks(database, seed.tenant_id, seed.profile_id)
    assert not any(c.source_type == "service_line" for c in after)
    bp_chunks = [c for c in after if c.source_type == "boilerplate"]
    assert len(bp_chunks) == 1 and "zero-trust" in bp_chunks[0].text
    kept = {c.id for c in before if c.source_type in {"profile_file", "past_performance"}}
    assert kept <= {c.id for c in after}
    # force re-embeds everything that still exists
    async with database.session(seed.tenant_id) as session:
        forced = await index_profile(
            session, seed.profile_id, embeddings=fake_embeddings, storage=router, force=True
        )
    assert len(forced.indexed) == 3 and forced.unchanged == []


async def test_similarity_search_ranks_relevant_chunks_and_filters_by_source_type(
    database: Database, settings: Settings, fake_embeddings: FakeEmbeddings
) -> None:
    seed = await _seed(database, settings, with_file=False)
    async with database.session(seed.tenant_id) as session:
        await index_profile(session, seed.profile_id, embeddings=fake_embeddings)
        hits = await similarity_search(
            session,
            seed.profile_id,
            "FedRAMP cloud migration hosting",
            k=8,
            embeddings=fake_embeddings,
        )
        assert hits[0].source_type in {"boilerplate", "past_performance"}
        assert hits[-1].source_type == "service_line"  # paving is the least similar
        assert hits[0].score > hits[-1].score and -1.0 <= hits[-1].score <= 1.0
        assert hits[0].citation.startswith(f"{hits[0].source_type}:{hits[0].chunk.source_id}#0")
        only_pp = await similarity_search(
            session,
            seed.profile_id,
            "asphalt paving",
            k=5,
            source_types=[SourceType.PAST_PERFORMANCE],
            embeddings=fake_embeddings,
        )
        assert [h.source_type for h in only_pp] == ["past_performance"]
        top1 = await similarity_search(
            session, seed.profile_id, "asphalt paving roads", k=1, embeddings=fake_embeddings
        )
        assert len(top1) == 1 and top1[0].source_type == "service_line"
        assert (
            await similarity_search(session, seed.profile_id, "   ", embeddings=fake_embeddings)
            == []
        )
    assert fake_embeddings.calls[-1][1] == "query"


async def test_search_never_returns_another_tenants_chunks(
    database: Database, settings: Settings, fake_embeddings: FakeEmbeddings
) -> None:
    a = await _seed(database, settings, with_file=False)
    b = await _seed(database, settings, with_file=False)
    async with database.session(a.tenant_id) as session:
        await index_profile(session, a.profile_id, embeddings=fake_embeddings)
    async with database.session(b.tenant_id) as session:
        await index_profile(session, b.profile_id, embeddings=fake_embeddings)
    async with database.session(b.tenant_id) as session:
        # B searching A's profile id: RLS hides every row
        assert (
            await similarity_search(session, a.profile_id, "cloud", embeddings=fake_embeddings)
            == []
        )
        mine = await similarity_search(session, b.profile_id, "cloud", embeddings=fake_embeddings)
        assert mine and {h.chunk.tenant_id for h in mine} == {b.tenant_id}
    async with database.session(None) as session:  # no tenant context: nothing at all
        assert (
            await similarity_search(session, a.profile_id, "cloud", embeddings=fake_embeddings)
            == []
        )
    async with database.owner_session() as session:
        total = (await session.execute(select(func.count()).select_from(KBChunk))).scalar_one()
        assert total == 6  # 3 sources x 2 tenants


async def test_profile_mutations_via_api_schedule_a_reindex(
    api_client: httpx.AsyncClient,
    database: Database,
    settings: Settings,
    fake_embeddings: FakeEmbeddings,
) -> None:
    seed = await _seed(database, settings, with_file=False)
    headers = auth_headers(user_id=seed.user_id, tenant_id=seed.tenant_id, role=Role.TENANT_OWNER)
    base = f"/api/v1/profiles/{seed.profile_id}"
    r = await api_client.post(
        f"{base}/service-lines",
        json={"name": "Zero trust", "description": "Zero-trust network architecture design."},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    line_id = r.json()["id"]
    chunks = await _chunks(database, seed.tenant_id, seed.profile_id)
    assert {c.source_type for c in chunks} == {"boilerplate", "past_performance", "service_line"}
    assert any(c.source_id == uuid.UUID(line_id) and "Zero-trust" in c.text for c in chunks)
    calls_before = len(fake_embeddings.calls)
    r = await api_client.put(
        f"{base}/service-lines/{line_id}", json={"name": "Zero trust plus"}, headers=headers
    )
    assert r.status_code == 200, r.text
    chunks = await _chunks(database, seed.tenant_id, seed.profile_id)
    assert any("Zero trust plus" in c.text for c in chunks)
    assert len(fake_embeddings.calls) == calls_before + 1  # only the changed line re-embedded
    r = await api_client.delete(f"{base}/service-lines/{line_id}", headers=headers)
    assert r.status_code == 204
    chunks = await _chunks(database, seed.tenant_id, seed.profile_id)
    assert not any(c.source_id == uuid.UUID(line_id) for c in chunks)
    assert [c.text for c in chunks if c.source_type == "service_line"] == [
        c.text for c in chunks if "Road paving" in c.text
    ]  # the seeded line is untouched
    # a non-KB sub-resource does not trigger indexing
    calls_before = len(fake_embeddings.calls)
    r = await api_client.post(
        f"{base}/keywords", json={"kind": "include", "term": "zero trust"}, headers=headers
    )
    assert r.status_code == 201 and len(fake_embeddings.calls) == calls_before


async def test_index_job_and_celery_task(
    database: Database, settings: Settings, fake_embeddings: FakeEmbeddings, monkeypatch: Any
) -> None:
    seed = await _seed(database, settings, with_file=False)
    summary = await index_profile_job(
        seed.tenant_id,
        seed.profile_id,
        database=database,
        embeddings=fake_embeddings,
        storage=StorageRouter(settings),
    )
    assert summary["indexed"] == 3 and summary["chunks"] >= 3 and summary["warnings"] == []
    assert INDEX_PROFILE_TASK in celery_app.tasks and index_profile_task.name == INDEX_PROFILE_TASK
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.jobs.index_profile.index_profile_sync",
        lambda tenant_id, profile_id: calls.append((tenant_id, profile_id)) or {"chunks": 1},
    )
    result = index_profile_task.apply(args=[str(seed.tenant_id), str(seed.profile_id)])
    assert result.successful() and result.result == {"chunks": 1}
    assert calls == [(str(seed.tenant_id), str(seed.profile_id))]
    # unparseable file: warning, other sources still indexed
    async with database.owner_session() as session:
        file_id = uuid.uuid4()
        file = File(
            id=file_id,
            tenant_id=seed.tenant_id,
            filename="broken.pdf",
            extension="pdf",
            kind="pdf",
            content_type="application/pdf",
            size_bytes=3,
            sha256="a" * 64,
            region=Region.US,
            bucket="bidradar-us",
            key=f"tenants/{seed.tenant_id}/files/{file_id}.pdf",
        )
        session.add(file)
        await session.flush()
        session.add(
            ProfileFile(
                tenant_id=seed.tenant_id,
                profile_id=seed.profile_id,
                file_id=file.id,
                kind=ProfileFileKind.BROCHURE,
            )
        )
    summary = await index_profile_job(
        seed.tenant_id,
        seed.profile_id,
        database=database,
        embeddings=fake_embeddings,
        storage=StorageRouter(settings),
    )
    assert summary["unchanged"] == 3 and len(summary["warnings"]) == 1
    assert "profile_file" in summary["warnings"][0]


def test_source_text_builders() -> None:
    bp = BoilerplateBlock(
        kind=BoilerplateKind.COMPANY_OVERVIEW,
        title="Overview",
        body="<h2>Who we are</h2><p>Small &amp; agile.</p>",
        body_format="html",
    )
    text = boilerplate_text(bp)
    assert text.startswith("Overview\nBoilerplate: company overview") and "Small & agile." in text
    pp = PastPerformance(
        title="T", customer="C", role=PerformanceRole.SUB, scope="S", technologies=[]
    )
    assert past_performance_text(pp) == "Past performance: T\nCustomer: C\nRole: sub\nScope: S"
    line = ServiceLine(name="N", description="D", differentiators=["x", "y"], tools=[])
    assert service_line_text(line) == "Service line: N\nDescription: D\nDifferentiators: x, y"


# --- opportunity embeddings -----------------------------------------------------------------------


def _opp(external_id: str = "n-1", **overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": "sam_opps",
        "external_id": external_id,
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Cloud Migration Services",
        "description_text": "Migrate 40 field offices to a FedRAMP Moderate cloud.",
    }
    values.update(overrides)
    return OpportunityIn(**values)


async def test_opportunity_embedding_filled_on_create_and_amend(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    bus = EventBus()
    embedder = install_opportunity_embeddings(
        Settings(_env_file=None),  # type: ignore[call-arg]
        bus,
        embeddings=fake_embeddings,
    )
    assert isinstance(embedder, OpportunityEmbedder)
    async with database.session(None) as session:
        created = await ingest(session, _opp(), bus=bus, now=NOW)
        opp_id = created.opportunity.id
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None and row.embedding is not None and len(row.embedding) == 1024
        first = list(row.embedding)
    assert embedder.embedded == [opp_id]
    text = fake_embeddings.calls[-1][0][0]
    assert text.startswith("Cloud Migration Services") and "40 field offices" in text
    async with database.session(None) as session:  # amendment re-embeds with the new text
        amended = await ingest(
            session,
            _opp(description_text="Now also asphalt paving of 3 runways."),
            bus=bus,
            now=NOW,
        )
        assert amended.version == 2
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None and list(row.embedding) != first
    assert embedder.embedded == [opp_id, opp_id]
    # provider without a key: subscriber not installed
    assert (
        install_opportunity_embeddings(
            Settings(_env_file=None, embedding_provider="voyage"),  # type: ignore[call-arg]
            EventBus(),
        )
        is None
    )


async def test_opportunity_text_uses_summary_and_first_document_chunk(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    async with database.session(None) as session:
        created = await ingest(session, _opp(description_text=None), now=NOW)
        row = created.opportunity
        row.summary_ai = "Line one.\nLine two."
        assert opportunity_text(row, "Requirements: SOW page one") == (
            "Cloud Migration Services\n\nLine one.\nLine two.\n\nRequirements: SOW page one"
        )
        assert await embed_opportunity(session, row, embeddings=fake_embeddings) is True
        assert fake_embeddings.calls[-1][0] == ["Cloud Migration Services\n\nLine one.\nLine two."]
