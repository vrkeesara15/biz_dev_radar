"""M4-03: semantic, keyword and past-performance signals against the real Postgres.

Embeddings are FakeEmbeddings (deterministic hashed bag-of-words), so cosine ordering is
reproducible: a notice whose words overlap a service line is closer than an unrelated one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.config import Region
from app.core.db import Database
from app.core.matching.engine import evaluate
from app.core.opportunity import NoticeType
from app.core.profile_fields import CodeScheme, KeywordKind, PerformanceRole
from app.models import (
    CompanyProfile,
    DocumentChunk,
    KBChunk,
    Opportunity,
    OpportunityDocument,
    PastPerformance,
    ProfileCode,
    ProfileKeyword,
    ServiceLine,
)
from app.services.embeddings import FakeEmbeddings
from app.services.knowledge_base import index_profile
from app.services.matching.loaders import load_match_profile, match_opportunity_from_row
from app.services.matching.signals import (
    KEYWORD_DOC_CHARS,
    compute_signals,
    ensure_opportunity_embedding,
    keyword_signal,
    past_performance_signal,
    semantic_signal,
)
from sqlalchemy import select

from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

CLOUD_TITLE = "Cloud migration services for legacy mainframe workloads"
CLOUD_BODY = (
    "The contractor shall migrate legacy mainframe workloads to a commercial cloud, "
    "provide devops automation, kubernetes container orchestration and FedRAMP "
    "compliance support throughout the period of performance."
)
GROUNDS_TITLE = "Grounds maintenance and landscaping"
GROUNDS_BODY = (
    "The contractor shall mow lawns, trim hedges, remove snow and maintain flower beds "
    "at the campus throughout the year."
)


def _opportunity(title: str, body: str, **overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": f"m403-{uuid.uuid4().hex[:8]}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": title,
        "description_text": body,
        "naics": ["541511"],
        "response_due_at": NOW + timedelta(days=30),
        "version": 1,
    }
    values.update(overrides)
    return Opportunity(**values)  # type: ignore[arg-type]


async def _seed(database: Database, embeddings: FakeEmbeddings):  # type: ignore[no-untyped-def]
    """A cloud-migration profile with one service line and one past-performance record,
    plus a matching notice and an unrelated one."""
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=Region.US,
            legal_name="Cloud Movers LLC",
            version=1,
            target_countries=["US"],
            remote_ok=True,
        )
        cloud = _opportunity(CLOUD_TITLE, CLOUD_BODY)
        grounds = _opportunity(GROUNDS_TITLE, GROUNDS_BODY)
        session.add_all([profile, cloud, grounds])
        await session.flush()
        session.add_all(
            [
                ServiceLine(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    name="Cloud migration",
                    description=(
                        "We migrate legacy mainframe workloads to commercial cloud "
                        "platforms with devops automation and kubernetes containers."
                    ),
                ),
                PastPerformance(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    title="Treasury mainframe migration",
                    customer="Department of the Treasury",
                    role=PerformanceRole.PRIME,
                    scope=(
                        "Migrated mainframe workloads to a FedRAMP cloud with kubernetes "
                        "container orchestration and devops automation."
                    ),
                ),
                ProfileCode(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    scheme=CodeScheme.NAICS,
                    code="541511",
                    is_primary=True,
                ),
                ProfileKeyword(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    kind=KeywordKind.INCLUDE,
                    term="cloud migration",
                    weight=Decimal("2.0"),
                ),
                ProfileKeyword(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    kind=KeywordKind.INCLUDE,
                    term="kubernetes",
                    weight=Decimal("1.0"),
                ),
            ]
        )
        await session.flush()
        ids = (tenant.id, profile.id, cloud.id, grounds.id)
    async with database.session(ids[0]) as session:
        await index_profile(session, ids[1], embeddings=embeddings)
    return ids


async def test_semantic_and_past_performance_rank_the_matching_notice_first(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, cloud_id, grounds_id = await _seed(database, fake_embeddings)
    async with database.session(tenant_id) as session:
        scores: dict[str, tuple[float, float]] = {}
        for label, opp_id in (("cloud", cloud_id), ("grounds", grounds_id)):
            row = await session.get(Opportunity, opp_id)
            assert row is not None
            vector = await ensure_opportunity_embedding(session, row, embeddings=fake_embeddings)
            assert vector is not None and len(vector) == 1024
            semantic = await semantic_signal(session, profile_id, vector)
            past = await past_performance_signal(session, profile_id, vector)
            scores[label] = (float(semantic.raw), float(past.raw))
            assert semantic.detail["source"] == "service_line"
            assert past.detail["source"] == "past_performance"
    assert scores["cloud"][0] > scores["grounds"][0]
    assert scores["cloud"][1] > scores["grounds"][1]
    assert 0.0 <= scores["grounds"][0] <= 1.0
    assert scores["cloud"][0] <= 1.0


async def test_semantic_signal_without_indexed_sources_is_unknown(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Empty Co", version=1
        )
        session.add(profile)
        await session.flush()
        tenant_id, profile_id = tenant.id, profile.id
    vector = fake_embeddings.vector("anything at all")
    async with database.session(tenant_id) as session:
        semantic = await semantic_signal(session, profile_id, vector)
        past = await past_performance_signal(session, profile_id, vector)
    assert semantic.raw == Decimal("0.5") and semantic.note == "no service lines indexed"
    assert past.raw == Decimal("0.5") and past.note == "no past performance indexed"


async def test_embedding_is_computed_on_demand_and_reused(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    async with database.owner_session() as session:
        row = _opportunity(CLOUD_TITLE, CLOUD_BODY)
        session.add(row)
        await session.flush()
        opp_id = row.id
        assert row.embedding is None
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        before = len(fake_embeddings.calls)
        vector = await ensure_opportunity_embedding(session, row, embeddings=fake_embeddings)
        assert vector is not None
        assert len(fake_embeddings.calls) == before + 1
        # stored, so the second call embeds nothing
        again = await ensure_opportunity_embedding(session, row, embeddings=fake_embeddings)
        assert len(fake_embeddings.calls) == before + 1
        assert again == vector
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None and row.embedding is not None


async def test_embedding_missing_provider_leaves_the_signal_unknown(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    from app.services.embeddings import EmbeddingError

    class BrokenEmbeddings:
        name = "broken"

        async def embed(
            self, texts: list[str], *, input_type: str = "document"
        ) -> list[list[float]]:
            raise EmbeddingError("provider down")

    tenant_id, profile_id, cloud_id, _ = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        row = await session.get(Opportunity, cloud_id)
        assert row is not None
        row.embedding = None
        await session.flush()
    async with database.session(tenant_id) as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile = await load_match_profile(session, profile_row)
        row = await session.get(Opportunity, cloud_id)
        assert row is not None
        signals = await compute_signals(
            session,
            profile,
            row,
            embeddings=BrokenEmbeddings(),  # type: ignore[arg-type]
        )
    assert signals.semantic is not None and signals.semantic.raw == Decimal("0.5")
    assert signals.semantic.note == "no opportunity embedding"
    assert signals.past_performance is not None
    assert signals.past_performance.raw == Decimal("0.5")
    # the keyword signal does not need an embedding
    assert signals.keyword is not None and signals.keyword.raw > Decimal("0.3")
    assert signals.keyword.note == "2 include keywords"


async def test_keyword_signal_is_weighted_normalised_and_ordered(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    tenant_id, profile_id, cloud_id, grounds_id = await _seed(database, fake_embeddings)
    async with database.session(tenant_id) as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile = await load_match_profile(session, profile_row)
        hit = await keyword_signal(session, cloud_id, profile.include_keywords)
        miss = await keyword_signal(session, grounds_id, profile.include_keywords)
    assert Decimal("0") <= miss.raw < hit.raw <= Decimal("1")
    assert miss.raw == Decimal("0")
    per_term = {t["term"]: t["score"] for t in hit.detail["terms"]}
    assert per_term["cloud migration"] > 0  # title (weight A) and description
    assert per_term["kubernetes"] > 0  # description only
    assert per_term["cloud migration"] > per_term["kubernetes"]
    assert hit.note == "2 include keywords"


async def test_keyword_signal_reads_parsed_document_chunks(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    """A term that appears only inside a parsed solicitation document still scores."""
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Doc Co", version=1
        )
        opp = _opportunity("Support services", "General support services are required.")
        session.add_all([profile, opp])
        await session.flush()
        document = OpportunityDocument(
            opportunity_id=opp.id, url="https://example.test/sow.pdf", kind="pdf"
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=0,
                page=4,
                text="The contractor shall deliver hydrographic survey services.",
            )
        )
        session.add(
            ProfileKeyword(
                tenant_id=tenant.id,
                profile_id=profile.id,
                kind=KeywordKind.INCLUDE,
                term="hydrographic survey",
            )
        )
        await session.flush()
        tenant_id, profile_id, opp_id = tenant.id, profile.id, opp.id
    async with database.session(tenant_id) as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile = await load_match_profile(session, profile_row)
        signal = await keyword_signal(session, opp_id, profile.include_keywords)
    assert signal.raw > Decimal("0")
    assert signal.detail["terms"][0]["term"] == "hydrographic survey"


async def test_keyword_signal_without_include_keywords_is_unknown(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    async with database.owner_session() as session:
        opp = _opportunity(CLOUD_TITLE, CLOUD_BODY)
        session.add(opp)
        await session.flush()
        opp_id = opp.id
    async with database.session(None) as session:
        signal = await keyword_signal(session, opp_id, ())
    assert signal.raw == Decimal("0.5") and signal.note == "no include keywords"


async def test_full_score_ranks_the_matching_opportunity_above_the_unrelated_one(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    """SPEC 6 acceptance: the whole stage-1 + stage-2 pipeline orders the two notices."""
    tenant_id, profile_id, cloud_id, grounds_id = await _seed(database, fake_embeddings)
    outcomes = {}
    async with database.session(tenant_id) as session:
        profile_row = await session.get(CompanyProfile, profile_id)
        assert profile_row is not None
        profile = await load_match_profile(session, profile_row)
        for label, opp_id in (("cloud", cloud_id), ("grounds", grounds_id)):
            row = await session.get(Opportunity, opp_id)
            assert row is not None
            signals = await compute_signals(session, profile, row, embeddings=fake_embeddings)
            outcomes[label] = evaluate(
                profile, match_opportunity_from_row(row), NOW, **signals.as_kwargs()
            )
    assert outcomes["cloud"].score > outcomes["grounds"].score
    breakdown = outcomes["cloud"].breakdown["signals"]
    assert breakdown["semantic_similarity"]["note"] == "max cosine vs service lines"
    assert breakdown["keyword_match"]["raw"] > 0
    assert breakdown["past_performance_relevance"]["raw"] > 0
    # every signal actually ran: none of the three carries the "not computed" note
    for name in ("semantic_similarity", "keyword_match", "past_performance_relevance"):
        assert breakdown[name].get("note") != "not computed"


async def test_document_text_is_capped_before_the_tsvector(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    """A huge parsed document cannot blow the 1 MB tsvector limit."""
    async with database.owner_session() as session:
        opp = _opportunity("Bulk notice", "short body")
        session.add(opp)
        await session.flush()
        document = OpportunityDocument(
            opportunity_id=opp.id, url="https://example.test/big.pdf", kind="pdf"
        )
        session.add(document)
        await session.flush()
        for index in range(6):
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=index,
                    text=("dredging " * 8_000),
                )
            )
        await session.flush()
        opp_id = opp.id
    assert 6 * 8_000 * len("dredging ") > KEYWORD_DOC_CHARS  # capped in SQL
    async with database.session(None) as session:
        from app.core.matching.types import KeywordWeight

        signal = await keyword_signal(session, opp_id, (KeywordWeight("dredging"),))
    assert Decimal("0") < signal.raw <= Decimal("1")


async def test_kb_chunks_of_another_tenant_never_reach_the_signal(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    _tenant_id, profile_id, cloud_id, _ = await _seed(database, fake_embeddings)
    async with database.owner_session() as session:
        other, _, _ = await create_tenant_with_owner(session)
        other_id = other.id
        chunks = (await session.execute(select(KBChunk))).scalars().all()
        assert chunks  # the seed indexed something
    async with database.session(other_id) as session:
        row = await session.get(Opportunity, cloud_id)
        assert row is not None
        vector = await ensure_opportunity_embedding(session, row, embeddings=fake_embeddings)
        assert vector is not None
        semantic = await semantic_signal(session, profile_id, vector)
    assert semantic.raw == Decimal("0.5")  # RLS hides tenant A's service lines
