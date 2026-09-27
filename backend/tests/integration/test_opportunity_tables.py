"""M2-03: canonical opportunity tables (SPEC 5.3 / 10.2)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import Region
from app.core.opportunity import NoticeType, OpportunityStatus
from app.models import (
    AwardsEnrichment,
    DocumentChunk,
    Opportunity,
    OpportunityDocument,
    OpportunityVersion,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

SPEC_5_3_COLUMNS = {
    "id",
    "source_id",
    "external_id",
    "source_url",
    "region",
    "country",
    "currency",
    "notice_type",
    "title",
    "description_text",
    "summary_ai",
    "solicitation_number",
    "parent_opportunity_id",
    "buyer_org",
    "buyer_sub_org",
    "buyer_office",
    "buyer_hierarchy",
    "naics",
    "psc",
    "aln",
    "india_category",
    "set_aside",
    "reservation",
    "place_of_performance",
    "estimated_value_min",
    "estimated_value_max",
    "emd_amount",
    "tender_fee",
    "posted_at",
    "questions_due_at",
    "prebid_meeting_at",
    "response_due_at",
    "opening_at",
    "archive_at",
    "contacts",
    "eligibility",
    "incumbent",
    "prior_award_value",
    "prior_pop_end",
    "status",
    "content_hash",
    "version",
    "embedding",
    "raw_ref",
}


def make_opportunity(**overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": uuid.uuid4().hex,
        "source_url": "https://sam.gov/opp/x/view",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Cloud migration services",
        "buyer_org": "Department of Testing",
        "naics": ["541512"],
        "posted_at": datetime(2026, 9, 1, tzinfo=UTC),
        "response_due_at": datetime(2026, 10, 1, 17, 0, tzinfo=UTC),
        "source_tz": "America/New_York",
    }
    values.update(overrides)
    return Opportunity(**values)


async def _columns(session: AsyncSession, table: str) -> dict[str, str]:
    rows = await session.execute(
        text(
            "SELECT column_name, format_type(a.atttypid, a.atttypmod) "
            "FROM information_schema.columns c "
            "JOIN pg_attribute a ON a.attrelid = c.table_name::regclass "
            "AND a.attname = c.column_name "
            "WHERE c.table_schema = 'public' AND c.table_name = :t"
        ),
        {"t": table},
    )
    return {name: typ for name, typ in rows.all()}


async def test_opportunities_has_every_spec_column(database) -> None:  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        cols = await _columns(session, "opportunities")
    missing = SPEC_5_3_COLUMNS - set(cols)
    assert not missing, f"missing 5.3 columns: {sorted(missing)}"
    assert cols["embedding"] == "vector(1024)"
    assert cols["naics"] == "character varying(16)[]"
    assert cols["place_of_performance"] == "jsonb"
    assert cols["estimated_value_max"] == "numeric(18,2)"
    assert cols["posted_at"] == "timestamp with time zone"
    assert cols["notice_type"] == "notice_type"
    assert cols["status"] == "opportunity_status"
    assert "tenant_id" not in cols
    # USD-normalised copies and the source time zone (SPEC 5.3 notes)
    assert {"estimated_value_min_usd", "estimated_value_max_usd", "source_tz"} <= set(cols)


async def test_related_tables_have_acceptance_columns(database) -> None:  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        versions = await _columns(session, "opportunity_versions")
        docs = await _columns(session, "opportunity_documents")
        chunks = await _columns(session, "document_chunks")
        awards = await _columns(session, "awards_enrichment")
    assert {"opportunity_id", "version", "diff", "content_hash", "created_at"} <= set(versions)
    assert versions["diff"] == "jsonb"
    assert {
        "opportunity_id",
        "file_name",
        "url",
        "hash",
        "size",
        "pages",
        "status",
        "parsed_text_ref",
    } <= set(docs)
    assert {"document_id", "page", "text", "embedding"} <= set(chunks)
    assert chunks["embedding"] == "vector(1024)"
    assert {
        "opportunity_id",
        "incumbent",
        "prior_award_value",
        "prior_pop_end",
        "num_offers",
        "source_ref",
        "recompete_watch",
    } <= set(awards)
    for cols in (versions, docs, chunks, awards):
        assert "tenant_id" not in cols


async def test_indexes_unique_trigram_and_fulltext(database) -> None:  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        rows = await session.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'opportunities'")
        )
        indexes = {name: definition for name, definition in rows.all()}
    assert "uq_opportunities_source_external" in indexes
    assert "UNIQUE" in indexes["uq_opportunities_source_external"]
    assert "(source_id, external_id)" in indexes["uq_opportunities_source_external"]
    trgm = indexes["ix_opportunities_title_trgm"]
    assert "USING gin" in trgm and "gin_trgm_ops" in trgm
    fts = indexes["ix_opportunities_fts"]
    assert "USING gin" in fts and "to_tsvector" in fts and "description_text" in fts


async def test_unique_source_external_id(database) -> None:  # type: ignore[no-untyped-def]
    async with database.session(None) as session:
        session.add(make_opportunity(external_id="dup"))
        await session.flush()
        session.add(make_opportunity(external_id="dup"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    async with database.session(None) as session:
        session.add(make_opportunity(external_id="dup", source_id="grants_gov"))
        await session.flush()


async def test_opportunities_are_global_no_rls_app_role_reads_and_writes(database) -> None:  # type: ignore[no-untyped-def]
    async with database.owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                "AND c.relname IN ('opportunities', 'opportunity_versions', "
                "'opportunity_documents', 'document_chunks', 'awards_enrichment')"
            )
        )
        flags = {name: (rls, force) for name, rls, force in rows.all()}
        policies = set(
            (
                await conn.execute(
                    text("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
    assert len(flags) == 5
    assert all(flag == (False, False) for flag in flags.values()), flags
    assert not (set(flags) & policies)

    # App role without any tenant context: full DML, and rows are visible to everyone.
    async with database.session(None) as session:
        opp = make_opportunity(
            embedding=[0.001] * 1024,
            estimated_value_max=Decimal("1250000.50"),
            place_of_performance={"city": "Reston", "state": "VA", "country": "US"},
            contacts=[{"name": "Jane Doe", "email": "jane@example.gov"}],
        )
        session.add(opp)
        await session.flush()
        opp_id = opp.id
    tenant_a = uuid.uuid4()
    async with database.session(tenant_a) as session:
        stored = await session.get(Opportunity, opp_id)
        assert stored is not None
        assert stored.status is OpportunityStatus.OPEN
        assert stored.version == 1
        assert stored.estimated_value_max == Decimal("1250000.50")
        assert stored.place_of_performance["state"] == "VA"
        assert list(stored.embedding)[:2] == pytest.approx([0.001, 0.001])
        stored.title = "Cloud migration services (amended)"
    async with database.session(None) as session:
        distance = (
            await session.execute(
                select(Opportunity.embedding.cosine_distance([0.001] * 1024)).where(
                    Opportunity.id == opp_id
                )
            )
        ).scalar_one()
        assert distance == pytest.approx(0.0, abs=1e-6)


async def test_children_cascade_and_parent_links(database) -> None:  # type: ignore[no-untyped-def]
    async with database.session(None) as session:
        parent = make_opportunity(solicitation_number="W911NF-26-R-0001")
        session.add(parent)
        await session.flush()
        amendment = make_opportunity(
            solicitation_number="W911NF-26-R-0001", parent_opportunity_id=parent.id
        )
        session.add(amendment)
        doc = OpportunityDocument(
            opportunity_id=parent.id,
            url="https://sam.gov/api/prod/opps/v3/x/download",
            file_name="sow.pdf",
        )
        session.add(doc)
        await session.flush()
        session.add_all(
            [
                OpportunityVersion(
                    opportunity_id=parent.id,
                    version=2,
                    diff={"response_due_at": {"old": "a", "new": "b"}},
                    changes=["deadline_moved"],
                    content_hash="0" * 64,
                ),
                DocumentChunk(document_id=doc.id, chunk_index=0, page=1, text="Scope"),
                AwardsEnrichment(
                    opportunity_id=parent.id,
                    source_id="sam_awards",
                    award_id="W911NF-20-C-0001",
                    incumbent="Acme Corp",
                    prior_award_value=Decimal("100000"),
                    num_offers=3,
                    recompete_watch=True,
                ),
            ]
        )
        await session.flush()
        parent_id, amendment_id, doc_id = parent.id, amendment.id, doc.id

    # same version twice is rejected
    async with database.session(None) as session:
        session.add(
            OpportunityVersion(opportunity_id=parent_id, version=2, diff={}, content_hash="1" * 64)
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with database.session(None) as session:
        parent = await session.get(Opportunity, parent_id)
        assert parent is not None
        await session.delete(parent)
    async with database.session(None) as session:
        assert await session.get(OpportunityDocument, doc_id) is None
        for model in (OpportunityVersion, OpportunityDocument, DocumentChunk, AwardsEnrichment):
            rows = (await session.execute(select(model))).scalars().all()
            assert rows == [], f"{model.__tablename__} not cascaded"
        amendment = await session.get(Opportunity, amendment_id)
        assert amendment is not None
        assert amendment.parent_opportunity_id is None, "ON DELETE SET NULL"
