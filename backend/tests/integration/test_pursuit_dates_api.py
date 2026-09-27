"""M6-02: key dates auto-created per pursuit, edited by hand, and re-derived when an
amendment moves the buyer's deadline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from app.core.config import Region
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.roles import Role
from app.models import Membership, Opportunity, Pursuit, PursuitDate
from app.services.events import OPPORTUNITY_AMENDED, EventBus
from app.services.key_dates import install_key_date_recalc, recalculate_for_opportunity
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
QUESTIONS = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)


async def _setup(
    database: Database, *, region: Region = Region.US, **notice: Any
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session, region=region)
        from app.models import CompanyProfile

        profile = CompanyProfile(tenant_id=tenant.id, region=region, legal_name="Dates LLC")
        writer = make_user()
        session.add_all([profile, writer])
        await session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=writer.id, role=Role.WRITER))
        values: dict[str, Any] = {
            "source_id": "sam_opps",
            "external_id": f"dates-{uuid.uuid4().hex[:8]}",
            "region": region,
            "country": "US" if region is Region.US else "IN",
            "currency": "USD" if region is Region.US else "INR",
            "notice_type": NoticeType.RFP,
            "title": "Dated notice",
            "source_tz": "America/New_York" if region is Region.US else "Asia/Kolkata",
            "response_due_at": DUE,
        }
        values.update(notice)
        opportunity = Opportunity(**values)
        session.add(opportunity)
        await session.flush()
        return {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "writer_id": writer.id,
            "profile_id": profile.id,
            "opportunity_id": opportunity.id,
        }


def _headers(ctx: dict[str, Any], role: Role = Role.TENANT_OWNER) -> dict[str, str]:
    user_id = ctx["writer_id"] if role in (Role.WRITER, Role.VIEWER) else ctx["owner_id"]
    return auth_headers(user_id=user_id, tenant_id=ctx["tenant_id"], role=role)


async def _open(client: httpx.AsyncClient, ctx: dict[str, Any]) -> str:
    response = await client.post(
        f"/api/v1/opportunities/{ctx['opportunity_id']}/pursue",
        json={"run_agents": False},
        headers=_headers(ctx),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["pursuit"]["id"])


async def test_pursuing_creates_the_spec_9_dates(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database, questions_due_at=QUESTIONS)
    pursuit_id = await _open(api_client, ctx)
    body = (
        await api_client.get(f"/api/v1/pursuits/{pursuit_id}/dates", headers=_headers(ctx))
    ).json()
    assert [item["kind"] for item in body["items"]] == [
        "questions_due",
        "internal_draft",
        "internal_review",
        "internal_final",
        "portal_submission",
    ]
    by_kind = {item["kind"]: item for item in body["items"]}
    assert by_kind["internal_final"]["at"]["utc"].startswith("2026-10-12T18:00:00")
    assert by_kind["internal_final"]["at"]["buyer_tz"] == "America/New_York"
    assert "EDT" in by_kind["portal_submission"]["at"]["display"]
    assert by_kind["portal_submission"]["at"]["buyer_display"].startswith("Oct 14, 2026")
    assert all(item["source"] == "auto" for item in body["items"])
    assert all(item["acknowledged_at"] is None for item in body["items"])
    assert by_kind["internal_draft"]["note"] == "5 days before the deadline"

    # the pursuit's internal deadline matches the internal_final date
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, uuid.UUID(pursuit_id))
        assert pursuit is not None
        assert pursuit.internal_due_at == DUE - timedelta(hours=48)


async def test_an_india_pursuit_adds_emd_and_dsc(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database, region=Region.IN)
    pursuit_id = await _open(api_client, ctx)
    body = (
        await api_client.get(f"/api/v1/pursuits/{pursuit_id}/dates", headers=_headers(ctx))
    ).json()
    by_kind = {item["kind"]: item for item in body["items"]}
    assert set(by_kind) == {
        "emd_bg_ready",
        "internal_draft",
        "internal_review",
        "dsc_check",
        "internal_final",
        "portal_submission",
    }
    assert by_kind["dsc_check"]["at"]["utc"].startswith("2026-10-11T18:00:00")
    assert "IST" in by_kind["dsc_check"]["at"]["display"]


async def test_a_notice_without_a_deadline_gets_no_dates(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database, response_due_at=None)
    pursuit_id = await _open(api_client, ctx)
    body = (
        await api_client.get(f"/api/v1/pursuits/{pursuit_id}/dates", headers=_headers(ctx))
    ).json()
    assert body["items"] == []


async def test_crud_on_key_dates_with_role_checks(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    url = f"/api/v1/pursuits/{pursuit_id}/dates"

    site_visit = DUE - timedelta(days=8)
    created = await api_client.post(
        url,
        json={"kind": "custom", "at": site_visit.isoformat(), "label": "Site visit"},
        headers=_headers(ctx, Role.WRITER),
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "user"
    assert created.json()["label"] == "Site visit"
    date_id = created.json()["id"]

    # a second custom date is fine; a second auto kind is not
    second = await api_client.post(
        url,
        json={"kind": "custom", "at": (site_visit + timedelta(days=1)).isoformat()},
        headers=_headers(ctx),
    )
    assert second.status_code == 201
    assert second.json()["label"] == "Key date"
    clash = await api_client.post(
        url, json={"kind": "internal_final", "at": DUE.isoformat()}, headers=_headers(ctx)
    )
    assert clash.status_code == 409
    assert clash.json()["detail"]["error"] == "duplicate_kind"

    # naive datetimes and unknown kinds are 422
    assert (
        await api_client.post(
            url, json={"kind": "custom", "at": "2026-10-01T10:00:00"}, headers=_headers(ctx)
        )
    ).status_code == 422
    assert (
        await api_client.post(
            url, json={"kind": "kickoff", "at": DUE.isoformat()}, headers=_headers(ctx)
        )
    ).status_code == 422

    moved = site_visit + timedelta(hours=2)
    updated = await api_client.put(
        f"{url}/{date_id}", json={"at": moved.isoformat()}, headers=_headers(ctx)
    )
    assert updated.status_code == 200
    assert datetime.fromisoformat(updated.json()["at"]["utc"].replace("Z", "+00:00")) == moved

    acked = await api_client.post(
        f"{url}/{date_id}/acknowledge",
        headers=auth_headers(
            user_id=ctx["writer_id"], tenant_id=ctx["tenant_id"], role=Role.VIEWER
        ),
    )
    assert acked.status_code == 200
    assert acked.json()["acknowledged_by"] == str(ctx["writer_id"])
    assert acked.json()["acknowledged_at"] is not None

    # a viewer may acknowledge but not write
    viewer = auth_headers(user_id=ctx["writer_id"], tenant_id=ctx["tenant_id"], role=Role.VIEWER)
    assert (
        await api_client.post(url, json={"kind": "custom", "at": DUE.isoformat()}, headers=viewer)
    ).status_code == 403
    assert (await api_client.delete(f"{url}/{date_id}", headers=viewer)).status_code == 403

    assert (
        await api_client.delete(f"{url}/{date_id}", headers=_headers(ctx, Role.WRITER))
    ).status_code == 204
    assert (await api_client.get(f"{url}", headers=_headers(ctx))).json()[
        "items"
    ].__len__() == 5  # 4 auto + the second custom

    # an unknown or foreign date id is 404
    assert (
        await api_client.put(f"{url}/{uuid.uuid4()}", json={}, headers=_headers(ctx))
    ).status_code == 404


async def test_editing_an_auto_date_pins_it_against_recalculation(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    url = f"/api/v1/pursuits/{pursuit_id}/dates"
    items = {
        i["kind"]: i for i in (await api_client.get(url, headers=_headers(ctx))).json()["items"]
    }
    pinned = items["internal_draft"]["id"]
    chosen = DUE - timedelta(days=7)
    response = await api_client.put(
        f"{url}/{pinned}",
        json={"at": chosen.isoformat(), "note": "our own plan"},
        headers=_headers(ctx),
    )
    assert response.status_code == 200
    assert response.json()["source"] == "user"

    # acknowledge the review date so the reset is observable
    review_id = items["internal_review"]["id"]
    await api_client.post(f"{url}/{review_id}/acknowledge", headers=_headers(ctx))

    new_due = DUE + timedelta(days=7)
    async with database.session(ctx["tenant_id"]) as session:
        opportunity = await session.get(Opportunity, ctx["opportunity_id"])
        assert opportunity is not None
        opportunity.response_due_at = new_due
        await session.flush()
        results = await recalculate_for_opportunity(session, opportunity)
        assert len(results) == 1
        result = next(iter(results.values()))
        assert result.kept == ["internal_draft"]
        assert {row.kind for row in result.moved} == {
            "internal_review",
            "internal_final",
            "portal_submission",
        }

    after = {
        i["kind"]: i for i in (await api_client.get(url, headers=_headers(ctx))).json()["items"]
    }
    assert (
        datetime.fromisoformat(after["internal_draft"]["at"]["utc"].replace("Z", "+00:00"))
        == chosen
    )
    assert after["internal_draft"]["note"] == "our own plan"
    assert (
        datetime.fromisoformat(after["portal_submission"]["at"]["utc"].replace("Z", "+00:00"))
        == new_due
    )
    # the shifted review date lost its acknowledgement and says why
    assert after["internal_review"]["acknowledged_at"] is None
    assert (
        after["internal_review"]["note"] == "the buyer moved the deadline; re-acknowledge this date"
    )

    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, uuid.UUID(pursuit_id))
        assert pursuit is not None
        assert pursuit.internal_due_at == new_due - timedelta(hours=48)


async def test_the_amendment_event_recalculates_every_tracking_tenant(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database)
    pursuit_id = await _open(api_client, ctx)
    bus = EventBus()
    install_key_date_recalc(bus, database)

    new_due = DUE - timedelta(days=4)
    async with database.owner_session(ctx["tenant_id"]) as session:
        opportunity = await session.get(Opportunity, ctx["opportunity_id"])
        assert opportunity is not None
        opportunity.response_due_at = new_due

    await bus.publish(
        OPPORTUNITY_AMENDED,
        {
            "opportunity_id": str(ctx["opportunity_id"]),
            "version": 2,
            "changes": ["deadline_moved"],
            "diff": {"response_due_at": {"old": DUE.isoformat(), "new": new_due.isoformat()}},
        },
    )

    async with database.session(ctx["tenant_id"]) as session:
        rows = {
            row.kind: row
            for row in (
                await session.execute(
                    select(PursuitDate).where(PursuitDate.pursuit_id == uuid.UUID(pursuit_id))
                )
            )
            .scalars()
            .all()
        }
    assert rows["portal_submission"].at == new_due
    assert rows["internal_final"].at == new_due - timedelta(hours=48)

    # an amendment that does NOT move the deadline changes nothing
    await bus.publish(
        OPPORTUNITY_AMENDED,
        {
            "opportunity_id": str(ctx["opportunity_id"]),
            "version": 3,
            "changes": ["new_attachment"],
            "diff": {"documents": {"old": [], "new": ["a.pdf"]}},
        },
    )
    async with database.session(ctx["tenant_id"]) as session:
        again = (
            await session.execute(
                select(PursuitDate).where(
                    PursuitDate.pursuit_id == uuid.UUID(pursuit_id),
                    PursuitDate.kind == "portal_submission",
                )
            )
        ).scalar_one()
        assert again.at == new_due


async def test_a_withdrawn_buyer_date_is_dropped_and_a_new_one_appears(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _setup(database, questions_due_at=QUESTIONS)
    pursuit_id = await _open(api_client, ctx)
    async with database.session(ctx["tenant_id"]) as session:
        opportunity = await session.get(Opportunity, ctx["opportunity_id"])
        assert opportunity is not None
        opportunity.questions_due_at = None
        opportunity.prebid_meeting_at = DUE - timedelta(days=11)
        await session.flush()
        await recalculate_for_opportunity(session, opportunity)
    kinds = [
        item["kind"]
        for item in (
            await api_client.get(f"/api/v1/pursuits/{pursuit_id}/dates", headers=_headers(ctx))
        ).json()["items"]
    ]
    assert "questions_due" not in kinds
    assert "prebid_meeting" in kinds


async def test_dates_are_tenant_scoped(api_client: httpx.AsyncClient, database: Database) -> None:
    a = await _setup(database)
    b = await _setup(database)
    pursuit_id = await _open(api_client, a)
    foreign = await api_client.get(f"/api/v1/pursuits/{pursuit_id}/dates", headers=_headers(b))
    assert foreign.status_code == 404
