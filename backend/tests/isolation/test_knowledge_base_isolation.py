"""M1-12 (SPEC 8): RAG indexes are per tenant. kb_chunks is RLS-scoped, so a second
tenant's similarity_search over the first tenant's profile returns nothing, and its own
search never surfaces the other tenant's chunks."""

from __future__ import annotations

import uuid

from app.core.config import Region
from app.core.db import Database
from app.core.profile_fields import BoilerplateKind
from app.models import BoilerplateBlock, CompanyProfile, KBChunk
from app.services.embeddings import FakeEmbeddings
from app.services.knowledge_base import index_profile, similarity_search
from sqlalchemy import select

from tests.factories import create_tenant_with_owner


async def _tenant_with_kb(database: Database, marker: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with database.owner_session() as session:
        tenant, _, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name=f"{marker} LLC")
        session.add(profile)
        await session.flush()
        session.add(
            BoilerplateBlock(
                tenant_id=tenant.id,
                profile_id=profile.id,
                kind=BoilerplateKind.COMPANY_OVERVIEW,
                title=f"{marker} overview",
                body=f"<p>{marker} confidential cloud migration past performance.</p>",
            )
        )
        return tenant.id, profile.id


async def test_similarity_search_is_tenant_scoped(database: Database) -> None:
    fake = FakeEmbeddings()
    tenant_a, profile_a = await _tenant_with_kb(database, "AlphaSecret")
    tenant_b, profile_b = await _tenant_with_kb(database, "BravoSecret")
    async with database.session(tenant_a) as session:
        assert (await index_profile(session, profile_a, embeddings=fake)).chunks >= 1
    async with database.session(tenant_b) as session:
        assert (await index_profile(session, profile_b, embeddings=fake)).chunks >= 1

    async with database.session(tenant_b) as session:
        # B asking for A's profile: nothing (RLS), not even a leaked count
        assert await similarity_search(session, profile_a, "cloud migration", embeddings=fake) == []
        hits = await similarity_search(session, profile_b, "cloud migration", embeddings=fake)
        assert hits and all(h.chunk.tenant_id == tenant_b for h in hits)
        assert all("AlphaSecret" not in h.chunk.text for h in hits)
        visible = (await session.execute(select(KBChunk))).scalars().all()
        assert {c.tenant_id for c in visible} == {tenant_b}
        # B cannot index A's profile either: the profile row is invisible
        assert (await index_profile(session, profile_a, embeddings=fake)).warnings == [
            "profile not found"
        ]
    async with database.session(tenant_a) as session:
        hits = await similarity_search(session, profile_a, "cloud migration", embeddings=fake)
        assert hits and all("BravoSecret" not in h.chunk.text for h in hits)
