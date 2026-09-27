"""M2-15: GET /api/v1/opportunities search + GET /api/v1/opportunities/{id} detail."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.disclaimers import VERIFY_ON_PORTAL, attribution_text, record_footer
from app.core.opportunity import DocumentRef, NoticeType, OpportunityIn, OpportunityStatus
from app.services.ingest import ingest
from sqlalchemy import text

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _opp(external_id: str, **overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": "sam_opps",
        "external_id": external_id,
        "source_url": f"https://sam.gov/opp/{external_id}/view",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": f"Notice {external_id}",
        "buyer_org": "Buyer " + external_id,
        "naics": ["541512"],
        "posted_at": NOW - timedelta(days=2),
        "response_due_at": NOW + timedelta(days=30),
    }
    values.update(overrides)
    return OpportunityIn(**values)


async def _seed(database: Database) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    async with database.session(None) as session:
        rows = [
            _opp(
                "cloud",
                title="Cloud Migration Services for Field Offices",
                description_text="Migrate legacy workloads to a FedRAMP cloud.",
                documents=[DocumentRef(url="https://sam.gov/doc/sow.pdf", file_name="sow.pdf")],
                solicitation_number="47PF-26-R-0001",
            ),
            _opp(
                "janitor",
                title="Janitorial services, building 12",
                description_text="Daily cleaning.",
                notice_type=NoticeType.RFQ,
                naics=["561720"],
                response_due_at=NOW + timedelta(days=3),
            ),
            _opp(
                "gem",
                source_id="gem",
                source_url=None,
                region=Region.IN,
                country="IN",
                currency="INR",
                title="Supply of desktop computers",
                notice_type=NoticeType.GEM_BID,
                naics=[],
                response_due_at=NOW + timedelta(days=10),
            ),
            _opp(
                "closed",
                title="Cloud archive storage (closed)",
                response_due_at=NOW - timedelta(days=1),
            ),
            _opp(
                "cancelled",
                title="Cloud desk phones",
                status=OpportunityStatus.CANCELLED,
            ),
        ]
        for opp in rows:
            result = await ingest(session, opp, now=NOW)
            ids[opp.external_id] = result.opportunity.id
        # amendment on the cloud notice: a version row
        moved = await ingest(
            session,
            _opp(
                "cloud",
                title="Cloud Migration Services for Field Offices",
                description_text="Migrate legacy workloads to a FedRAMP cloud.",
                documents=[DocumentRef(url="https://sam.gov/doc/sow.pdf", file_name="sow.pdf")],
                solicitation_number="47PF-26-R-0001",
                response_due_at=NOW + timedelta(days=37),
            ),
            now=NOW,
        )
        assert moved.version == 2
        # a cross-source duplicate of the cloud notice (thinner -> hidden from search)
        dup = await ingest(
            session,
            _opp(
                "mirror-cloud",
                source_id="mirror",
                title="Cloud Migration Services for Field Offices",
                solicitation_number="RFP 47PF26R0001",
                buyer_org="Buyer cloud",
                naics=[],
            ),
            now=NOW,
        )
        ids["mirror"] = dup.opportunity.id
        assert dup.opportunity.duplicate_of == ids["cloud"]
    return ids


@pytest.fixture()
async def seeded(api_client: httpx.AsyncClient, database: Database) -> dict[str, Any]:
    ids = await _seed(database)
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
    headers = auth_headers(user_id=user.id, tenant_id=tenant.id, role="viewer")
    return {"ids": ids, "headers": headers, "client": api_client}


async def _search(ctx: dict[str, Any], **params: Any) -> dict[str, Any]:
    resp = await ctx["client"].get("/api/v1/opportunities", params=params, headers=ctx["headers"])
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _titles(body: dict[str, Any]) -> list[str]:
    return [item["title"] for item in body["items"]]


async def test_list_returns_attribution_disclaimer_and_hides_duplicates(
    seeded: dict[str, Any],
) -> None:
    body = await _search(seeded)
    assert (
        body["total"] == 5 and body["page"] == 1 and body["page_size"] == 25 and body["pages"] == 1
    )
    assert "Cloud Migration Services for Field Offices" in _titles(body)
    assert all(item["disclaimer"] == VERIFY_ON_PORTAL for item in body["items"])
    assert all(item["match"] is None for item in body["items"])
    by_title = {item["title"]: item for item in body["items"]}
    sam = by_title["Cloud Migration Services for Field Offices"]
    assert sam["attribution"] == {
        "source_id": "sam_opps",
        "source_name": "SAM.gov Contract Opportunities",
        "source_url": "https://sam.gov/opp/cloud/view",
        "text": attribution_text("sam_opps", "https://sam.gov/opp/cloud/view"),
        "footer": record_footer("sam_opps", "https://sam.gov/opp/cloud/view"),
    }
    gem = by_title["Supply of desktop computers"]
    assert gem["attribution"]["source_name"].startswith("Government e-Marketplace")
    assert gem["attribution"]["source_url"] == "https://gem.gov.in/"  # portal home when no page
    assert sam["version"] == 2 and sam["duplicate_of"] is None
    # default order: soonest deadline first (nulls last), duplicates hidden
    assert _titles(body)[0] == "Cloud archive storage (closed)"  # past deadline sorts first
    assert "mirror" not in {item["source_id"] for item in body["items"]}
    with_dups = await _search(seeded, include_duplicates="true")
    assert with_dups["total"] == 6


async def test_full_text_search_uses_websearch_syntax_and_ranks(seeded: dict[str, Any]) -> None:
    cloud = await _search(seeded, q="cloud")
    assert set(_titles(cloud)) == {
        "Cloud Migration Services for Field Offices",
        "Cloud archive storage (closed)",
        "Cloud desk phones",
    }
    # description text is searched too
    fedramp = await _search(seeded, q="fedramp")
    assert _titles(fedramp) == ["Cloud Migration Services for Field Offices"]
    # websearch operators: negation and phrases
    minus = await _search(seeded, q="cloud -migration")
    assert set(_titles(minus)) == {"Cloud archive storage (closed)", "Cloud desk phones"}
    phrase = await _search(seeded, q='"desk phones"')
    assert _titles(phrase) == ["Cloud desk phones"]
    assert (await _search(seeded, q="cloud OR janitorial"))["total"] == 4
    assert (await _search(seeded, q="zzzz-nothing"))["total"] == 0


async def test_filters(seeded: dict[str, Any]) -> None:
    assert _titles(await _search(seeded, region="in")) == ["Supply of desktop computers"]
    assert _titles(await _search(seeded, type="rfq")) == ["Janitorial services, building 12"]
    assert (await _search(seeded, type="rfq,gem_bid"))["total"] == 2
    assert _titles(await _search(seeded, naics="561720")) == ["Janitorial services, building 12"]
    assert (await _search(seeded, naics="541512,561720"))["total"] == 4
    due = await _search(seeded, due_before=(NOW + timedelta(days=5)).isoformat())
    assert set(_titles(due)) == {
        "Janitorial services, building 12",
        "Cloud archive storage (closed)",
    }
    assert _titles(await _search(seeded, status="closing_soon")) == [
        "Janitorial services, building 12"
    ]
    assert _titles(await _search(seeded, status="cancelled")) == ["Cloud desk phones"]
    assert (await _search(seeded, status="open,closing_soon"))["total"] == 3
    # min_score is accepted (applied once matches exist) and does not filter yet
    assert (await _search(seeded, min_score=90))["total"] == 5
    combined = await _search(seeded, q="cloud", status="open", region="us")
    assert _titles(combined) == ["Cloud Migration Services for Field Offices"]


async def test_pagination_and_validation(seeded: dict[str, Any]) -> None:
    first = await _search(seeded, page=1, page_size=2)
    second = await _search(seeded, page=2, page_size=2)
    third = await _search(seeded, page=3, page_size=2)
    assert (first["total"], first["pages"]) == (5, 3)
    assert len(first["items"]) == 2 and len(second["items"]) == 2 and len(third["items"]) == 1
    ids = [i["id"] for i in first["items"] + second["items"] + third["items"]]
    assert len(set(ids)) == 5
    client, headers = seeded["client"], seeded["headers"]
    for params in ({"page_size": 101}, {"page": 0}, {"min_score": 101}, {"region": "eu"}):
        resp = await client.get("/api/v1/opportunities", params=params, headers=headers)
        assert resp.status_code == 422, params
    resp = await client.get("/api/v1/opportunities", params={"type": "tender"}, headers=headers)
    assert resp.status_code == 422 and "notice type" in resp.text
    resp = await client.get("/api/v1/opportunities", params={"status": "paused"}, headers=headers)
    assert resp.status_code == 422
    assert (await client.get("/api/v1/opportunities")).status_code == 401


async def test_detail_includes_versions_documents_match_null(seeded: dict[str, Any]) -> None:
    client, headers, ids = seeded["client"], seeded["headers"], seeded["ids"]
    resp = await client.get(f"/api/v1/opportunities/{ids['cloud']}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Cloud Migration Services for Field Offices"
    assert body["description_text"].startswith("Migrate legacy")
    assert body["match"] is None and body["disclaimer"] == VERIFY_ON_PORTAL
    assert body["attribution"]["source_url"] == "https://sam.gov/opp/cloud/view"
    assert body["version"] == 2
    assert [v["version"] for v in body["versions"]] == [2]
    assert body["versions"][0]["changes"] == ["deadline_moved"]
    assert set(body["versions"][0]["diff"]) == {"response_due_at"}
    assert [d["file_name"] for d in body["documents"]] == ["sow.pdf"]
    assert body["documents"][0]["status"] == "pending" and body["documents"][0]["url"].endswith(
        "sow.pdf"
    )
    assert body["also_from"] == [
        {
            "source_id": "mirror",
            "external_id": "mirror-cloud",
            "source_url": "https://sam.gov/opp/mirror-cloud/view",
        }
    ]
    assert body["content_hash"] and body["detail_status"] == "pending"
    # the duplicate itself is still addressable and points at the survivor
    resp = await client.get(f"/api/v1/opportunities/{ids['mirror']}", headers=headers)
    assert resp.status_code == 200 and resp.json()["duplicate_of"] == str(ids["cloud"])
    assert (
        await client.get(f"/api/v1/opportunities/{uuid.uuid4()}", headers=headers)
    ).status_code == 404
    assert (await client.get(f"/api/v1/opportunities/{ids['cloud']}")).status_code == 401


async def test_search_query_can_use_the_fts_and_naics_indexes(database: Database) -> None:
    """The query expression matches the GIN indexes exactly (planner picks them when a
    sequential scan is discouraged; on 5 rows it would otherwise seq-scan)."""
    from app.api.v1.opportunities import search_statement

    stmt = search_statement(
        q="cloud migration",
        region=None,
        notice_types=[],
        naics=[],
        due_before=None,
        statuses=[],
        include_duplicates=True,
    )
    async with database.owner_session() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = stmt.compile(
            dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
        )  # type: ignore[union-attr]
        plan = (await session.execute(text(f"EXPLAIN {compiled}"))).scalars().all()
    plan_text = "\n".join(plan)
    assert "ix_opportunities_fts" in plan_text, plan_text
    # and the NAICS filter can use its GIN index (typed array on the right-hand side)
    stmt = search_statement(
        q=None,
        region=None,
        notice_types=[],
        naics=["541512"],
        due_before=None,
        statuses=[],
        include_duplicates=True,
    )
    async with database.owner_session() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = stmt.compile(
            dialect=session.bind.dialect, compile_kwargs={"literal_binds": True}
        )  # type: ignore[union-attr]
        plan = (await session.execute(text(f"EXPLAIN {compiled}"))).scalars().all()
    assert "ix_opportunities_naics" in "\n".join(plan), plan


# --- M3-10: attribution and disclaimers on India records --------------------------------


async def test_every_in_record_carries_its_portal_name_and_official_link(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    async with database.session(None) as session:
        tn = await ingest(
            session,
            _opp(
                "2026_TNCMC_871234_1",
                source_id="gepnic_tn",
                source_url="https://tntenders.gov.in/nicgep/app?component=%24DirectLink&sp=Sx",
                region=Region.IN,
                country="IN",
                currency="INR",
                title="Formation of park at KRG Nagar",
                buyer_org="Government of Tamil Nadu",
                naics=[],
            ),
            now=NOW,
        )
        cppp = await ingest(
            session,
            _opp(
                "cppp-1",
                source_id="cppp",
                source_url=None,  # detail behind a CAPTCHA: the portal home is the link
                region=Region.IN,
                country="IN",
                currency="INR",
                title="Supply of transformers",
                buyer_org="Bharat Sanchar Nigam Limited",
                naics=[],
            ),
            now=NOW,
        )
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
    headers = auth_headers(user_id=user.id, tenant_id=tenant.id, role="viewer")

    resp = await api_client.get("/api/v1/opportunities", params={"region": "in"}, headers=headers)
    assert resp.status_code == 200, resp.text
    items = {item["source_id"]: item for item in resp.json()["items"]}
    assert {"gepnic_tn", "cppp"} <= set(items)
    for item in items.values():
        attribution = item["attribution"]
        assert attribution["source_name"] and attribution["source_name"] != item["source_id"]
        assert attribution["source_url"], "an IN record always links back to the portal"
        assert attribution["text"].startswith("Source: ")
        assert attribution["source_name"] in attribution["text"]
        assert attribution["source_url"] in attribution["text"]
        assert attribution["footer"].endswith(VERIFY_ON_PORTAL)
        assert item["disclaimer"] == VERIFY_ON_PORTAL

    assert items["gepnic_tn"]["attribution"]["source_name"] == (
        "Tamil Nadu Tenders (tntenders.gov.in)"
    )
    assert items["cppp"]["attribution"]["source_url"] == "https://eprocure.gov.in/"

    detail = await api_client.get(f"/api/v1/opportunities/{tn.opportunity.id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["attribution"] == items["gepnic_tn"]["attribution"]
    assert body["attribution"]["footer"] == record_footer(
        "gepnic_tn", "https://tntenders.gov.in/nicgep/app?component=%24DirectLink&sp=Sx"
    )
    assert cppp.opportunity.region is Region.IN
