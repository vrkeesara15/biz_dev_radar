"""M6-06: the daily expiry sweep and its bid block, stale pursuits, and the matrix
re-check an amendment forces once drafting has started. Frozen clocks throughout."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.profile_fields import CertificationKind, InsuranceKind, RegistrationKind
from app.core.roles import Role
from app.jobs.checks import expiry_checks_once, stale_pursuits_once
from app.models import (
    Certification,
    CompanyProfile,
    Insurance,
    Membership,
    Notification,
    Opportunity,
    Pursuit,
    Registration,
)
from app.notify.core import Dispatcher, SendResult
from app.services.checks import (
    STALE_AFTER_DAYS,
    apply_bid_block,
    flag_matrix_recheck,
    install_matrix_recheck,
    past_drafting,
)
from app.services.events import OPPORTUNITY_AMENDED, EventBus
from freezegun import freeze_time
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner, make_user

TODAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


class Recorder:
    name = "email"

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    async def send(self, delivery, notification, recipient) -> SendResult:  # type: ignore[no-untyped-def]
        self.calls.append((recipient.user_id, notification.event_type, dict(notification.payload)))
        return SendResult.sent(provider_ref=f"m-{len(self.calls)}")

    def clear(self) -> None:
        self.calls.clear()


def _dispatcher(recorder: Recorder) -> Dispatcher:
    return Dispatcher({recorder.name: recorder}, SETTINGS)


async def _tenant(database: Database, *, region: Region = Region.US) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session, region=region)
        profile = CompanyProfile(tenant_id=tenant.id, region=region, legal_name="Checks LLC")
        writer = make_user()
        session.add_all([profile, writer])
        await session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=writer.id, role=Role.WRITER))
        return {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "writer_id": writer.id,
            "profile_id": profile.id,
        }


async def _registration(
    database: Database, ctx: dict[str, Any], kind: RegistrationKind, expires_on: date
) -> uuid.UUID:
    async with database.owner_session(ctx["tenant_id"]) as session:
        row = Registration(
            tenant_id=ctx["tenant_id"],
            profile_id=ctx["profile_id"],
            kind=kind,
            identifier="X-1",
            expires_on=expires_on,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _run_expiry(database: Database, recorder: Recorder, today: date = TODAY):  # type: ignore[no-untyped-def]
    return await expiry_checks_once(
        database=database, settings=SETTINGS, dispatcher=_dispatcher(recorder), today=today
    )


async def test_the_daily_sweep_reminds_the_owner_at_60_30_and_7_days(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    await _registration(database, ctx, RegistrationKind.SAM, date(2026, 12, 1))  # 60 days
    async with database.owner_session(ctx["tenant_id"]) as session:
        session.add(
            Certification(
                tenant_id=ctx["tenant_id"],
                profile_id=ctx["profile_id"],
                kind=CertificationKind.ISO_9001,
                expires_on=date(2026, 11, 1),  # 30 days
            )
        )
        session.add(
            Insurance(
                tenant_id=ctx["tenant_id"],
                profile_id=ctx["profile_id"],
                kind=InsuranceKind.GENERAL_LIABILITY,
                expires_on=date(2026, 10, 9),  # 7 days
            )
        )
        session.add(
            Insurance(
                tenant_id=ctx["tenant_id"],
                profile_id=ctx["profile_id"],
                kind=InsuranceKind.CYBER,
                expires_on=date(2027, 5, 1),  # far away: silent
            )
        )

    recorder = Recorder()
    run = await _run_expiry(database, recorder)
    assert run.notified == 3
    by_window = {c[2]["window"]: c[2] for c in recorder.calls}
    assert sorted(by_window) == [7, 30, 60]
    assert by_window[60]["subkind"] == "sam"
    assert by_window[60]["blocks_bids"] is True
    assert by_window[30]["kind"] == "certification"
    assert by_window[7]["kind"] == "insurance"
    assert by_window[7]["blocks_bids"] is False
    assert all(c[0] == ctx["owner_id"] for c in recorder.calls)
    assert all(c[1] == "registration_expiry" for c in recorder.calls)

    # running again the same day repeats nothing (one notification per window)
    recorder.clear()
    assert (await _run_expiry(database, recorder)).notified == 0
    assert recorder.calls == []

    # a day later the same windows still say nothing new
    recorder.clear()
    assert (await _run_expiry(database, recorder, date(2026, 10, 3))).notified == 0


async def test_crossing_into_a_tighter_window_sends_again(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    await _registration(database, ctx, RegistrationKind.SAM, date(2026, 11, 1))
    recorder = Recorder()
    await _run_expiry(database, recorder, date(2026, 10, 2))  # 30 days
    assert [c[2]["window"] for c in recorder.calls] == [30]
    recorder.clear()
    await _run_expiry(database, recorder, date(2026, 10, 26))  # 6 days
    assert [c[2]["window"] for c in recorder.calls] == [7]


async def test_an_expired_sam_blocks_the_profile_and_pursuing_it(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database)
    registration_id = await _registration(database, ctx, RegistrationKind.SAM, date(2026, 9, 30))
    async with database.owner_session(ctx["tenant_id"]) as session:
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"blk-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Blocked notice",
            source_tz="America/New_York",
            response_due_at=datetime(2026, 12, 1, 18, 0, tzinfo=UTC),
        )
        session.add(opportunity)
        await session.flush()
        opportunity_id = opportunity.id

    recorder = Recorder()
    run = await _run_expiry(database, recorder)
    assert run.blocked == 1
    async with database.session(ctx["tenant_id"]) as session:
        profile = await session.get(CompanyProfile, ctx["profile_id"])
        assert profile is not None
        assert profile.blocked_for_bids is True

    headers = auth_headers(
        user_id=ctx["owner_id"], tenant_id=ctx["tenant_id"], role=Role.TENANT_OWNER
    )
    refused = await api_client.post(
        f"/api/v1/opportunities/{opportunity_id}/pursue",
        json={"run_agents": False},
        headers=headers,
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["error"] == "profile_blocked_for_bids"
    assert "SAM" in detail["reason"]
    # watching is still allowed: it starts no bid
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{opportunity_id}/watch", json={}, headers=headers
        )
    ).status_code == 201

    # renewing clears the block on the next run, and pursuing works again
    async with database.owner_session(ctx["tenant_id"]) as session:
        row = await session.get(Registration, registration_id)
        assert row is not None
        row.expires_on = date(2027, 9, 30)
    await _run_expiry(database, Recorder())
    async with database.session(ctx["tenant_id"]) as session:
        profile = await session.get(CompanyProfile, ctx["profile_id"])
        assert profile is not None
        assert profile.blocked_for_bids is False
    assert (
        await api_client.post(
            f"/api/v1/opportunities/{opportunity_id}/pursue",
            json={"run_agents": False},
            headers=headers,
        )
    ).status_code == 200


async def test_an_expired_gem_registration_does_not_block(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database, region=Region.IN)
    await _registration(database, ctx, RegistrationKind.GEM, date(2026, 9, 1))
    run = await _run_expiry(database, Recorder())
    assert run.blocked == 0
    async with database.session(ctx["tenant_id"]) as session:
        profile = await session.get(CompanyProfile, ctx["profile_id"])
        assert profile is not None
        assert profile.blocked_for_bids is False


async def test_an_expired_dsc_blocks_an_indian_profile(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database, region=Region.IN)
    await _registration(database, ctx, RegistrationKind.DSC, date(2026, 10, 1))
    async with database.session(ctx["tenant_id"]) as session:
        assert await apply_bid_block(session, TODAY) == [ctx["profile_id"]]


async def test_the_profile_sam_expiry_column_is_swept_too(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    async with database.owner_session(ctx["tenant_id"]) as session:
        profile = await session.get(CompanyProfile, ctx["profile_id"])
        assert profile is not None
        profile.sam_expires_on = date(2026, 10, 9)
    recorder = Recorder()
    await _run_expiry(database, recorder)
    assert [c[2]["label"] for c in recorder.calls] == ["SAM registration"]
    assert recorder.calls[0][2]["window"] == 7


# --- stale pursuits -----------------------------------------------------------------------


async def _pursuit(
    database: Database, ctx: dict[str, Any], *, activity_at: datetime, stage: str = "drafting"
) -> uuid.UUID:
    async with database.owner_session(ctx["tenant_id"]) as session:
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"stale-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Neglected notice",
            buyer_org="GSA",
            source_tz="America/New_York",
            response_due_at=datetime(2026, 12, 1, 18, 0, tzinfo=UTC),
        )
        session.add(opportunity)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=ctx["tenant_id"],
            profile_id=ctx["profile_id"],
            opportunity_id=opportunity.id,
            owner_user_id=ctx["owner_id"],
            stage=stage,
            decision="bid",
            activity_at=activity_at,
        )
        session.add(pursuit)
        await session.flush()
        return pursuit.id


async def test_a_pursuit_idle_for_five_days_nudges_its_owner(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    stale_id = await _pursuit(database, ctx, activity_at=NOW - timedelta(days=STALE_AFTER_DAYS))
    await _pursuit(database, ctx, activity_at=NOW - timedelta(days=2))  # fresh
    await _pursuit(database, ctx, activity_at=NOW - timedelta(days=30), stage="submitted")  # closed

    recorder = Recorder()
    run = await stale_pursuits_once(
        database=database, settings=SETTINGS, dispatcher=_dispatcher(recorder), now=NOW
    )
    assert run.scanned == 1
    assert run.notified == 1
    assert recorder.calls[0][0] == ctx["owner_id"]
    payload = recorder.calls[0][2]
    assert payload["reason"] == "stale"
    assert payload["idle_days"] == 5
    assert payload["title"] == "Neglected notice"

    # once a day, not once a run
    recorder.clear()
    await stale_pursuits_once(
        database=database,
        settings=SETTINGS,
        dispatcher=_dispatcher(recorder),
        now=NOW + timedelta(hours=1),
    )
    assert recorder.calls == []
    # the next day it nudges again
    recorder.clear()
    await stale_pursuits_once(
        database=database,
        settings=SETTINGS,
        dispatcher=_dispatcher(recorder),
        now=NOW + timedelta(days=1),
    )
    assert len(recorder.calls) == 1
    assert recorder.calls[0][2]["idle_days"] == 6
    assert stale_id is not None


async def test_working_on_a_pursuit_resets_its_activity_clock(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    ctx = await _tenant(database)
    pursuit_id = await _pursuit(
        database, ctx, activity_at=NOW - timedelta(days=10), stage="qualifying"
    )
    headers = auth_headers(
        user_id=ctx["owner_id"], tenant_id=ctx["tenant_id"], role=Role.TENANT_OWNER
    )
    before = datetime.now(UTC)
    response = await api_client.post(
        f"/api/v1/pursuits/{pursuit_id}/comments",
        json={"body": "picking this back up"},
        headers=headers,
    )
    assert response.status_code == 201
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, pursuit_id)
        assert pursuit is not None
        assert pursuit.activity_at >= before

    recorder = Recorder()
    run = await stale_pursuits_once(
        database=database, settings=SETTINGS, dispatcher=_dispatcher(recorder), now=NOW
    )
    assert run.notified == 0


# --- amended after drafting started ---------------------------------------------------------


def test_past_drafting_covers_the_right_stages() -> None:
    assert not past_drafting("identified")
    assert not past_drafting("qualifying")
    assert not past_drafting("bid_decision")
    assert past_drafting("drafting")
    assert past_drafting("in_review")
    assert past_drafting("final_approval")
    assert past_drafting("submitted")
    assert not past_drafting("cancelled")


async def test_an_amendment_after_drafting_forces_a_matrix_recheck(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    drafting_id = await _pursuit(database, ctx, activity_at=NOW, stage="drafting")
    early_id = await _pursuit(database, ctx, activity_at=NOW, stage="qualifying")

    recorder = Recorder()
    async with database.session(ctx["tenant_id"]) as session:
        drafting = await session.get(Pursuit, drafting_id)
        assert drafting is not None
        opportunity = await session.get(Opportunity, drafting.opportunity_id)
        assert opportunity is not None
        run = await flag_matrix_recheck(
            session,
            SETTINGS,
            _dispatcher(recorder),
            ctx["tenant_id"],
            opportunity,
            version=2,
            changes=["new_attachment"],
            now=NOW,
        )
    assert run.notified == 1
    payload = recorder.calls[0][2]
    assert payload["matrix_recheck_required"] is True
    assert payload["changes"] == ["new_attachment"]
    assert "Re-run the compliance matrix" in payload["question"]
    assert recorder.calls[0][1] == "agent_question"

    async with database.session(ctx["tenant_id"]) as session:
        drafting = await session.get(Pursuit, drafting_id)
        early = await session.get(Pursuit, early_id)
        assert drafting is not None and early is not None
        assert drafting.matrix_recheck_required is True
        assert early.matrix_recheck_required is False


async def test_the_amendment_event_reaches_the_recheck_subscriber(
    database: Database, clean_db: Database
) -> None:
    ctx = await _tenant(database)
    pursuit_id = await _pursuit(database, ctx, activity_at=NOW, stage="in_review")
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, pursuit_id)
        assert pursuit is not None
        opportunity_id = pursuit.opportunity_id

    bus = EventBus()
    install_matrix_recheck(SETTINGS, bus, database)
    with freeze_time(NOW, real_asyncio=True):
        await bus.publish(
            OPPORTUNITY_AMENDED,
            {
                "opportunity_id": str(opportunity_id),
                "version": 3,
                "changes": ["qa_posted"],
                "diff": {"documents": {"old": [], "new": ["qa.pdf"]}},
            },
        )
    async with database.session(ctx["tenant_id"]) as session:
        pursuit = await session.get(Pursuit, pursuit_id)
        assert pursuit is not None
        assert pursuit.matrix_recheck_required is True
        notifications = (
            (
                await session.execute(
                    select(Notification).where(Notification.event_type == "agent_question")
                )
            )
            .scalars()
            .all()
        )
        assert len(notifications) == 1


async def test_the_checks_are_tenant_scoped(database: Database, clean_db: Database) -> None:
    a = await _tenant(database)
    b = await _tenant(database)
    await _registration(database, a, RegistrationKind.SAM, date(2026, 10, 9))
    recorder = Recorder()
    await _run_expiry(database, recorder)
    assert {c[0] for c in recorder.calls} == {a["owner_id"]}
    async with database.session(b["tenant_id"]) as session:
        assert (await session.execute(select(Registration))).scalars().all() == []
