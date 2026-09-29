"""GET /pursuits/{id}/artifacts and /artifacts/{artifact_id} (SPEC 8, 10.4; OQ-147).

Agents 3, 4, 6, 7 and 8 store their output as versioned `pursuit_artifacts` rows, but
until now only the matrix and packet routes read any of them, so the workspace's
bid/no-bid and pricing panels had no data path. These tests pin the contract the
frontend's typed client is generated from: the latest version per kind, one kind at a
time when asked, every tenant role may read, an older version by id, and a scorecard or
red-team read lands in the audit log.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.api.v1.pursuits import AUDIT_ARTIFACT_READ
from app.core.compliance import (
    ARTIFACT_CHECKLIST,
    ARTIFACT_PRICING_TEMPLATE,
    ARTIFACT_RED_TEAM,
    ARTIFACT_SCORECARD,
)
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import AuditLog, CompanyProfile, Opportunity, Pursuit
from app.services.pursuits import store_artifact
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

SCORECARD_V1: dict[str, Any] = {"recommendation": "watch", "total": 51.0}
SCORECARD_V2: dict[str, Any] = {
    "recommendation": "bid",
    "total": 72.5,
    "criteria": [{"criterion": "fit", "score": 80, "weight": 0.3}],
    "gaps": ["No FedRAMP authorisation"],
}
PRICING: dict[str, Any] = {"template": "T&M", "labour_categories": 4}
CHECKLIST: dict[str, Any] = {"items": [{"label": "SF-33 signed", "required": True}]}
RED_TEAM: dict[str, Any] = {"report": {"overall_score": 71, "sections": []}}


async def _setup(database: Database) -> dict[str, Any]:
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
        )
        session.add_all([profile, opp])
        await session.flush()
        pursuit = Pursuit(
            tenant_id=tenant.id,
            profile_id=profile.id,
            opportunity_id=opp.id,
            created_by=user.id,
            stage="drafting",
            decision="bid",
        )
        session.add(pursuit)
        await session.flush()
        ctx: dict[str, Any] = {
            "tenant_id": tenant.id,
            "user_id": user.id,
            "email": user.email,
            "pursuit_id": pursuit.id,
        }
    return ctx


async def _store(database: Database, ctx: dict[str, Any]) -> dict[str, uuid.UUID]:
    """Two scorecard versions (a re-run), plus one each of three other kinds."""
    ids: dict[str, uuid.UUID] = {}
    async with database.session(ctx["tenant_id"]) as session:
        first = await store_artifact(
            session, ctx["tenant_id"], ctx["pursuit_id"], ARTIFACT_SCORECARD, SCORECARD_V1
        )
        second = await store_artifact(
            session, ctx["tenant_id"], ctx["pursuit_id"], ARTIFACT_SCORECARD, SCORECARD_V2
        )
        pricing = await store_artifact(
            session, ctx["tenant_id"], ctx["pursuit_id"], ARTIFACT_PRICING_TEMPLATE, PRICING
        )
        checklist = await store_artifact(
            session, ctx["tenant_id"], ctx["pursuit_id"], ARTIFACT_CHECKLIST, CHECKLIST
        )
        red_team = await store_artifact(
            session, ctx["tenant_id"], ctx["pursuit_id"], ARTIFACT_RED_TEAM, RED_TEAM
        )
        ids = {
            "scorecard_v1": first.id,
            "scorecard_v2": second.id,
            "pricing": pricing.id,
            "checklist": checklist.id,
            "red_team": red_team.id,
        }
        await session.commit()
    return ids


def _headers(ctx: dict[str, Any], role: Role | None = None) -> dict[str, str]:
    if role is None:
        return auth_headers(user_id=ctx["user_id"], tenant_id=ctx["tenant_id"], email=ctx["email"])
    return auth_headers(user_id=uuid.uuid4(), tenant_id=ctx["tenant_id"], role=role)


async def _audit_rows(database: Database) -> list[AuditLog]:
    async with database.owner_session() as session:
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.action == AUDIT_ARTIFACT_READ)))
            .scalars()
            .all()
        )


async def test_no_artifacts_is_an_empty_list_not_a_404(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    """The workspace probes for a scorecard before any agent has run: the panel needs an
    empty state, not an error."""
    ctx = await _setup(database)
    response = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts", headers=_headers(ctx)
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"pursuit_id": str(ctx["pursuit_id"]), "items": [], "count": 0, "kind": None}

    filtered = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts",
        params={"kind": ARTIFACT_SCORECARD},
        headers=_headers(ctx),
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []


async def test_list_returns_the_latest_version_of_every_kind(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    await _store(database, ctx)
    response = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts", headers=_headers(ctx)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4  # four kinds, not five rows: the v1 scorecard is history
    kinds = [row["kind"] for row in body["items"]]
    assert kinds == [
        ARTIFACT_CHECKLIST,
        ARTIFACT_SCORECARD,
        ARTIFACT_PRICING_TEMPLATE,
        ARTIFACT_RED_TEAM,
    ]
    scorecard = next(row for row in body["items"] if row["kind"] == ARTIFACT_SCORECARD)
    assert scorecard["version"] == 2
    assert scorecard["data"] == SCORECARD_V2
    assert scorecard["created_by"] == "agent"
    assert scorecard["created_at"]


async def test_kind_filter_returns_the_single_latest(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    await _store(database, ctx)
    response = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts",
        params={"kind": ARTIFACT_SCORECARD},
        headers=_headers(ctx),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == ARTIFACT_SCORECARD
    assert body["count"] == 1
    assert [row["version"] for row in body["items"]] == [2]
    assert body["items"][0]["data"]["recommendation"] == "bid"


async def test_an_unknown_kind_is_a_422_naming_the_kinds(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    response = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts",
        params={"kind": "invoice"},
        headers=_headers(ctx),
    )
    assert response.status_code == 422
    assert ARTIFACT_SCORECARD in response.json()["detail"]


async def test_one_version_by_id_including_an_older_one(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    ids = await _store(database, ctx)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts"
    old = await api_client.get(f"{base}/{ids['scorecard_v1']}", headers=_headers(ctx))
    assert old.status_code == 200
    assert old.json()["version"] == 1
    assert old.json()["data"] == SCORECARD_V1

    missing = await api_client.get(f"{base}/{uuid.uuid4()}", headers=_headers(ctx))
    assert missing.status_code == 404


async def test_an_artifact_of_another_pursuit_is_a_404(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    ids = await _store(database, ctx)
    other = await _setup(database)
    # same tenant would still be wrong: the id must belong to the pursuit in the path
    async with database.owner_session() as session:
        pursuit = await session.get(Pursuit, other["pursuit_id"])
        assert pursuit is not None
    response = await api_client.get(
        f"/api/v1/pursuits/{other['pursuit_id']}/artifacts/{ids['scorecard_v2']}",
        headers=_headers(other),
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "role", [Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER]
)
async def test_every_tenant_role_may_read_artifacts(
    api_client: httpx.AsyncClient, database: Database, role: Role
) -> None:
    ctx = await _setup(database)
    await _store(database, ctx)
    response = await api_client.get(
        f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts", headers=_headers(ctx, role)
    )
    assert response.status_code == 200
    assert response.json()["count"] == 4


async def test_scorecard_and_red_team_reads_are_audited_and_the_rest_are_not(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    """SPEC 11 audits reading generated content. The judgement about the tenant (the
    scorecard) and the criticism of their draft (the red team) are logged; the format
    rules and the checklist are mechanical and are not."""
    ctx = await _setup(database)
    ids = await _store(database, ctx)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts"

    assert (
        await api_client.get(base, params={"kind": ARTIFACT_CHECKLIST}, headers=_headers(ctx))
    ).status_code == 200
    assert await _audit_rows(database) == []

    assert (
        await api_client.get(base, params={"kind": ARTIFACT_SCORECARD}, headers=_headers(ctx))
    ).status_code == 200
    rows = await _audit_rows(database)
    assert len(rows) == 1
    assert rows[0].object_type == "pursuit_artifacts"
    assert rows[0].meta["kind"] == ARTIFACT_SCORECARD
    assert rows[0].meta["version"] == 2
    assert rows[0].user_id == ctx["user_id"]

    assert (
        await api_client.get(f"{base}/{ids['red_team']}", headers=_headers(ctx))
    ).status_code == 200
    kinds = sorted(row.meta["kind"] for row in await _audit_rows(database))
    assert kinds == [ARTIFACT_RED_TEAM, ARTIFACT_SCORECARD]


async def test_another_tenant_sees_nothing(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    ids = await _store(database, ctx)
    async with database.owner_session() as session:
        tenant_b, user_b, _ = await create_tenant_with_owner(session)
        headers = auth_headers(user_id=user_b.id, tenant_id=tenant_b.id, email=user_b.email)
    base = f"/api/v1/pursuits/{ctx['pursuit_id']}/artifacts"
    assert (await api_client.get(base, headers=headers)).status_code == 404
    assert (
        await api_client.get(f"{base}/{ids['scorecard_v2']}", headers=headers)
    ).status_code == 404
