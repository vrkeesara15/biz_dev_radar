"""M4-14 integration: routing a real event to real recipients, deliveries and the ops channel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.preferences import NotificationEvent as Category
from app.core.roles import Role
from app.models import (
    CompanyProfile,
    Membership,
    Notification,
    NotificationDelivery,
    Opportunity,
    Pursuit,
    User,
    UserNotificationPrefs,
)
from app.notify.core import Dispatcher, SendResult
from app.notify.router import NotificationRouter
from app.services.events import (
    ADAPTER_FAILING,
    AGENT_DRAFT_READY,
    MATCH_HIGH,
    MATCH_MEDIUM,
    OPPORTUNITY_AMENDED,
    REGISTRATION_EXPIRING,
    Event,
    EventBus,
)
from app.services.matching.alerts import create_saved_search
from sqlalchemy import select

from tests.factories import create_tenant_with_owner, make_user

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
OPS_HOOK = "https://hooks.slack.test/services/OPS"


class RecordingChannel:
    """A Channel that always succeeds and remembers what it was asked to send."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[tuple[uuid.UUID, str]] = []

    async def send(self, delivery, notification, recipient):  # type: ignore[no-untyped-def]
        self.sent.append((notification.id, notification.event_type))
        return SendResult.sent(provider_ref=f"{self.name}-ok")


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "email_provider": "memory"}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _router(
    database: Database, **overrides: object
) -> tuple[NotificationRouter, dict[str, RecordingChannel]]:
    settings = _settings(**overrides)
    channels = {
        name: RecordingChannel(name) for name in ("in_app", "email", "slack", "teams", "push")
    }
    dispatcher = Dispatcher(channels, settings, clock=lambda: NOW)  # type: ignore[arg-type]
    router = NotificationRouter(
        settings=settings, database=database, dispatcher=dispatcher, now=NOW
    )
    return router, channels


def _opportunity(**overrides: object) -> Opportunity:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": f"m414-{uuid.uuid4().hex[:10]}",
        "region": Region.US,
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Cloud migration services",
        "description_text": "Move mainframe workloads to a commercial cloud.",
        "buyer_org": "Department of the Treasury",
        "buyer_hierarchy": ["Department of the Treasury", "Internal Revenue Service"],
        "naics": ["541511"],
        "response_due_at": NOW + timedelta(days=30),
        "version": 3,
    }
    values.update(overrides)
    return Opportunity(**values)  # type: ignore[arg-type]


async def _seed(database: Database, *, roles: tuple[Role, ...] = (Role.BID_MANAGER,)):  # type: ignore[no-untyped-def]
    """A tenant with an owner plus one user per extra role, a profile and a notice."""
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session)
        extra: list[User] = []
        for role in roles:
            user = make_user()
            session.add(user)
            await session.flush()
            session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
            extra.append(user)
        profile = CompanyProfile(
            tenant_id=tenant.id, region=Region.US, legal_name="Cloud Movers LLC", version=1
        )
        opp = _opportunity()
        session.add_all([profile, opp])
        await session.flush()
        return tenant.id, owner, extra, profile.id, opp.id


def _match_event(name: str, tenant_id, profile_id, opp_id, score: float) -> Event:  # type: ignore[no-untyped-def]
    return Event(
        name=name,
        payload={
            "tenant_id": str(tenant_id),
            "profile_id": str(profile_id),
            "opportunity_id": str(opp_id),
            "match_id": str(uuid.uuid4()),
            "score": score,
            "band": "high" if name == MATCH_HIGH else "medium",
            "version": 3,
        },
        at=NOW,
    )


# --- match.high -------------------------------------------------------------------------


async def test_a_high_match_reaches_the_owner_and_bid_manager_on_four_channels(
    database: Database,
) -> None:
    tenant_id, owner, extra, profile_id, opp_id = await _seed(database)
    router, channels = _router(database)
    run = await router.route(_match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82))
    assert run.tenants == 1 and run.recipients == 2
    assert run.notifications == 2
    assert run.deliveries == 8  # 2 recipients x in_app + email + slack + teams
    assert {name for name, ch in channels.items() if ch.sent} == {
        "in_app",
        "email",
        "slack",
        "teams",
    }
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Notification))).scalars().all()
        assert {r.user_id for r in rows} == {owner.id, extra[0].id}
        assert {r.event_type for r in rows} == {Category.HIGH_FIT_MATCH.value}
        assert rows[0].opportunity_id == opp_id and rows[0].version == 3
        # the payload carries what the email template needs about the notice
        assert rows[0].payload["title"] == "Cloud migration services"
        assert rows[0].payload["buyer"].endswith("Internal Revenue Service")
        assert rows[0].payload["score"] == 82
        assert rows[0].payload["deep_link"].endswith(str(opp_id))
        assert set(rows[0].payload["actions"]) == {"pursue", "watch", "pass", "assign"}
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert all(d.status == "sent" for d in deliveries)
        assert all(d.scheduled_for is None for d in deliveries)


async def test_a_viewer_is_not_a_default_match_recipient(database: Database) -> None:
    tenant_id, _owner, _extra, profile_id, opp_id = await _seed(database, roles=(Role.VIEWER,))
    router, _ = _router(database)
    run = await router.route(_match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82))
    assert run.recipients == 1  # the tenant owner only
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Notification))).scalars().all()
    assert len(rows) == 1


async def test_the_same_event_twice_is_delivered_once(database: Database) -> None:
    tenant_id, _owner, _extra, profile_id, opp_id = await _seed(database)
    router, _ = _router(database)
    event = _match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82)
    first = await router.route(event)
    second = await router.route(event)
    assert first.notifications == 2 and second.notifications == 0
    assert second.duplicates == 2


async def test_per_user_channel_overrides_and_minimum_score(database: Database) -> None:
    tenant_id, owner, extra, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        session.add(
            UserNotificationPrefs(
                tenant_id=tenant_id,
                user_id=owner.id,
                channels_by_event={Category.HIGH_FIT_MATCH.value: ["slack", "web_push"]},
            )
        )
        session.add(
            UserNotificationPrefs(tenant_id=tenant_id, user_id=extra[0].id, min_score_instant=95)
        )
        await session.flush()
    router, channels = _router(database)
    run = await router.route(_match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82))
    assert run.recipients == 1  # the bid manager wants 95+
    assert any("below their minimum score" in s for s in run.skipped)
    async with database.session(tenant_id) as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert sorted(d.channel for d in deliveries) == ["push", "slack"]
    assert channels["email"].sent == []


async def test_quiet_hours_defer_an_instant_alert(database: Database) -> None:
    tenant_id, owner, _extra, profile_id, opp_id = await _seed(database, roles=())
    async with database.session(tenant_id) as session:
        session.add(
            UserNotificationPrefs(
                tenant_id=tenant_id,
                user_id=owner.id,
                tz="UTC",
                quiet_hours_start="09:00",
                quiet_hours_end="17:00",
            )
        )
        await session.flush()
    router, _ = _router(database)
    # the notice is due in 30 days, so the 72-hour override does not apply
    run = await router.route(_match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82))
    assert run.notifications == 1
    async with database.session(tenant_id) as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert all(d.status == "queued" for d in deliveries)
    assert all(d.scheduled_for == datetime(2026, 9, 27, 17, 0, tzinfo=UTC) for d in deliveries)


async def test_an_imminent_deadline_overrides_quiet_hours(database: Database) -> None:
    tenant_id, owner, _extra, profile_id, _opp = await _seed(database, roles=())
    async with database.owner_session() as session:
        urgent = _opportunity(response_due_at=NOW + timedelta(hours=10))
        session.add(urgent)
        await session.flush()
        urgent_id = urgent.id
    async with database.session(tenant_id) as session:
        session.add(
            UserNotificationPrefs(
                tenant_id=tenant_id,
                user_id=owner.id,
                quiet_hours_start="09:00",
                quiet_hours_end="17:00",
            )
        )
        await session.flush()
    router, _ = _router(database)
    await router.route(_match_event(MATCH_HIGH, tenant_id, profile_id, urgent_id, 82))
    async with database.session(tenant_id) as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert all(d.status == "sent" for d in deliveries)


# --- match.medium ------------------------------------------------------------------------


async def test_a_medium_match_is_queued_for_the_next_digest(database: Database) -> None:
    tenant_id, owner, _extra, profile_id, opp_id = await _seed(database, roles=())
    async with database.session(tenant_id) as session:
        session.add(
            UserNotificationPrefs(tenant_id=tenant_id, user_id=owner.id, digest_time="08:00")
        )
        await session.flush()
    router, channels = _router(database)
    run = await router.route(_match_event(MATCH_MEDIUM, tenant_id, profile_id, opp_id, 61))
    assert run.notifications == 1
    async with database.session(tenant_id) as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        notification = (await session.execute(select(Notification))).scalar_one()
    assert [d.channel for d in deliveries] == ["email"]
    assert deliveries[0].status == "queued"
    assert deliveries[0].scheduled_for == datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    assert notification.event_type == Category.DIGEST.value
    assert notification.payload["band"] == "medium"  # the digest collector keys off this
    assert channels["email"].sent == []


# --- alert rules override -------------------------------------------------------------------


async def test_an_alert_rule_overrides_the_channels_the_mode_and_the_audience(
    database: Database,
) -> None:
    tenant_id, owner, extra, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        await create_saved_search(
            session,
            tenant_id=tenant_id,
            user_id=extra[0].id,
            name="Cloud watch",
            filters={"q": "cloud migration"},
            channels=["slack"],
            mode="instant",
            min_score=50,
        )
        await session.flush()
    router, channels = _router(database)
    run = await router.route(_match_event(MATCH_MEDIUM, tenant_id, profile_id, opp_id, 61))
    assert run.recipients == 1
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Notification))).scalars().all()
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert [r.user_id for r in rows] == [extra[0].id]  # the rule names its user
    assert [d.channel for d in deliveries] == ["slack"]
    assert deliveries[0].status == "sent"  # instant, not the digest
    assert owner.id not in {r.user_id for r in rows}
    assert channels["slack"].sent


async def test_a_rule_that_does_not_accept_the_notice_leaves_the_defaults_alone(
    database: Database,
) -> None:
    tenant_id, _owner, extra, profile_id, opp_id = await _seed(database)
    async with database.session(tenant_id) as session:
        await create_saved_search(
            session,
            tenant_id=tenant_id,
            user_id=extra[0].id,
            name="Janitorial watch",
            filters={"q": "janitorial"},
            channels=["slack"],
            min_score=0,
        )
        await session.flush()
    router, _ = _router(database)
    run = await router.route(_match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82))
    assert run.recipients == 2  # back to the SPEC 7 default audience
    async with database.session(tenant_id) as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert {d.channel for d in deliveries} == {"in_app", "email", "slack", "teams"}


# --- amendments on tracked notices ------------------------------------------------------


async def test_an_amendment_reaches_only_the_tenants_that_track_the_notice(
    database: Database,
) -> None:
    tenant_a, owner_a, _extra_a, profile_a, opp_id = await _seed(database, roles=())
    tenant_b, _owner_b, _extra_b, _profile_b, _ = await _seed(database, roles=())
    async with database.session(tenant_a) as session:
        session.add(
            Pursuit(
                tenant_id=tenant_a,
                profile_id=profile_a,
                opportunity_id=opp_id,
                created_by=owner_a.id,
                owner_user_id=owner_a.id,
            )
        )
        await session.flush()
    router, _ = _router(database)
    run = await router.route(
        Event(
            name=OPPORTUNITY_AMENDED,
            payload={
                "opportunity_id": str(opp_id),
                "version": 4,
                "changes": ["response_due_at"],
                "diff": {
                    "response_due_at": {
                        "before": "2026-10-27T17:00:00+00:00",
                        "after": "2026-11-10T17:00:00+00:00",
                    }
                },
            },
            at=NOW,
        )
    )
    assert run.tenants == 1 and run.recipients == 1
    async with database.session(tenant_a) as session:
        rows = (await session.execute(select(Notification))).scalars().all()
    assert [r.user_id for r in rows] == [owner_a.id]
    assert rows[0].event_type == Category.AMENDMENT.value
    assert rows[0].version == 4
    assert rows[0].payload["diff"]["response_due_at"]["after"].startswith("2026-11-10")
    async with database.session(tenant_b) as session:
        assert (await session.execute(select(Notification))).scalars().all() == []


async def test_an_amendment_nobody_tracks_notifies_nobody(database: Database) -> None:
    _tenant, _owner, _extra, _profile, opp_id = await _seed(database, roles=())
    router, _ = _router(database)
    run = await router.route(
        Event(name=OPPORTUNITY_AMENDED, payload={"opportunity_id": str(opp_id)}, at=NOW)
    )
    assert run.notifications == 0
    assert run.skipped == ["nobody tracks this opportunity"]


# --- assignee and owner ------------------------------------------------------------------


async def test_a_draft_ready_event_goes_to_its_assignee(database: Database) -> None:
    tenant_id, _owner, extra, _profile, _opp = await _seed(database)
    router, _ = _router(database)
    run = await router.route(
        Event(
            name=AGENT_DRAFT_READY,
            payload={
                "tenant_id": str(tenant_id),
                "assignee_user_id": str(extra[0].id),
                "title": "Technical approach draft is ready",
                "version": 1,
            },
            at=NOW,
        )
    )
    assert run.recipients == 1
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Notification))).scalars().all()
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert [r.user_id for r in rows] == [extra[0].id]
    assert rows[0].event_type == Category.APPROVAL_REQUEST.value
    assert sorted(d.channel for d in deliveries) == ["email", "in_app"]


async def test_an_expiring_registration_goes_to_the_tenant_owner_by_email(
    database: Database,
) -> None:
    tenant_id, owner, extra, _profile, _opp = await _seed(database)
    router, _ = _router(database)
    run = await router.route(
        Event(
            name=REGISTRATION_EXPIRING,
            payload={
                "tenant_id": str(tenant_id),
                "title": "SAM registration expires in 30 days",
                "kind": "sam",
                "version": 1,
            },
            at=NOW,
        )
    )
    assert run.recipients == 1
    async with database.session(tenant_id) as session:
        rows = (await session.execute(select(Notification))).scalars().all()
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
    assert [r.user_id for r in rows] == [owner.id]
    assert extra[0].id not in {r.user_id for r in rows}
    assert [d.channel for d in deliveries] == ["email"]


async def test_an_event_without_a_tenant_is_skipped(database: Database) -> None:
    router, _ = _router(database)
    run = await router.route(Event(name=REGISTRATION_EXPIRING, payload={}, at=NOW))
    assert run.skipped == ["no tenant_id in the payload"]
    assert run.notifications == 0


# --- the ops channel ----------------------------------------------------------------------


@respx.mock
async def test_a_failing_adapter_pages_the_ops_slack_and_mailbox(database: Database) -> None:
    hook = respx.post(OPS_HOOK).mock(return_value=httpx.Response(200))
    router, _ = _router(database, ops_slack_webhook_url=OPS_HOOK, ops_email="ops@bidradar.example")
    run = await router.route(
        Event(
            name=ADAPTER_FAILING,
            payload={
                "source_id": "sam_opps",
                "run_id": str(uuid.uuid4()),
                "consecutive_failures": 3,
                "health": "failing",
                "message": "HTTP 503 from api.sam.gov",
            },
            at=NOW,
        )
    )
    assert sorted(run.ops) == ["email", "slack"]
    assert run.notifications == 0  # the platform has no tenant to write a row into
    assert hook.called
    body = hook.calls[0].request.content.decode()
    assert "Adapter sam_opps is failing (3 consecutive runs)" in body
    assert "HTTP 503 from api.sam.gov" in body
    async with database.session(None) as session:
        assert (await session.execute(select(Notification))).scalars().all() == []


@respx.mock
async def test_the_ops_channel_is_skipped_when_it_is_not_configured(
    database: Database,
) -> None:
    router, _ = _router(database)
    run = await router.route(Event(name=ADAPTER_FAILING, payload={"source_id": "sam_opps"}, at=NOW))
    assert run.ops == []
    assert run.skipped == ["no OPS_SLACK_WEBHOOK_URL", "no OPS_EMAIL"]


@respx.mock
async def test_a_refusing_ops_webhook_is_reported_not_raised(database: Database) -> None:
    respx.post(OPS_HOOK).mock(return_value=httpx.Response(404, text="no_service"))
    router, _ = _router(database, ops_slack_webhook_url=OPS_HOOK)
    run = await router.route(Event(name=ADAPTER_FAILING, payload={"source_id": "sam_opps"}, at=NOW))
    assert run.ops == []
    assert "ops slack HTTP 404" in run.skipped


# --- subscription --------------------------------------------------------------------------


async def test_the_router_waits_for_the_publishing_transaction_to_commit(
    database: Database,
) -> None:
    tenant_id, _owner, _extra, profile_id, opp_id = await _seed(database, roles=())
    bus = EventBus()
    router, _ = _router(database)
    router.subscribe(bus)
    async with database.session(tenant_id) as session:
        await bus.publish(
            MATCH_HIGH,
            _match_event(MATCH_HIGH, tenant_id, profile_id, opp_id, 82).payload,
            context={"session": session},
        )
        async with database.session(tenant_id) as probe:
            assert (await probe.execute(select(Notification))).scalars().all() == []
    await router.drain()
    async with database.session(tenant_id) as session:
        assert len((await session.execute(select(Notification))).scalars().all()) == 1


async def test_a_routing_failure_never_escapes(database: Database) -> None:
    bus = EventBus()
    router, _ = _router(database)
    router.subscribe(bus)
    # a payload whose tenant does not exist: the dispatch fails inside the guard
    await bus.publish(MATCH_HIGH, {"tenant_id": str(uuid.uuid4()), "score": 90})
    await router.drain()


@pytest.mark.parametrize("event_name", ["opportunity.created", "profile.changed"])
async def test_unrouted_events_are_not_subscribed(database: Database, event_name: str) -> None:
    bus = EventBus()
    router, _ = _router(database)
    router.subscribe(bus)
    await bus.publish(event_name, {"opportunity_id": str(uuid.uuid4())})
    await router.drain()
    assert router.runs == []
