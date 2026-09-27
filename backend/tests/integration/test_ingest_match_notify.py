"""M4-15 / SPEC 12: ingest -> match -> notify, end to end on the local stack.

One SAM fixture notice is ingested through the real pipeline with the real subscribers
installed (embeddings, match scoring, the SPEC 7 router) and the real Dispatcher with
the real in-app, email and Slack channels. Nothing is mocked but the network: the LLM is
the FakeLLM, embeddings are FakeEmbeddings, Slack's webhook is intercepted by respx and
the email provider is the in-memory one (Mailpit itself is covered by M4-10).

Asserted: a bell notification, a real rendered email and a Slack Block Kit post, all
within five minutes of scoring on a controlled clock, the match readable through
GET /api/v1/opportunities/{id}, and no second delivery when the same record is ingested
again unchanged.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.normalize.sam import normalize_sam_notice
from app.core.preferences import NotificationEvent as Category
from app.core.profile_fields import CodeScheme, KeywordKind, PerformanceRole
from app.core.roles import Role
from app.models import (
    CompanyProfile,
    Integration,
    Match,
    Notification,
    NotificationDelivery,
    PastPerformance,
    ProfileCode,
    ProfileKeyword,
    ServiceLine,
    UserNotificationPrefs,
)
from app.notify.core import Dispatcher, latency_seconds
from app.notify.email import EmailChannel, MemoryProvider
from app.notify.in_app import InAppChannel
from app.notify.registry import slack_resolver, teams_resolver
from app.notify.router import NotificationRouter
from app.notify.slack import SlackChannel
from app.notify.teams import TeamsChannel
from app.services.embeddings import FakeEmbeddings
from app.services.events import EventBus
from app.services.ingest import ingest
from app.services.knowledge_base import index_profile
from app.services.matching.engine import MatchScorer
from app.services.matching.rationale import RationaleGenerator
from app.services.matching.triggers import MatchTrigger
from app.services.opportunity_embeddings import install_opportunity_embeddings
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "sam_opps"
PAGE1 = json.loads((FIXTURES / "page1.json").read_text())
# "Enterprise Cloud Migration and Managed Services", NAICS 541512, SBA set-aside,
# response due 2026-10-20, ACC-APG RTP DIV (Army).
RECORD = PAGE1["opportunitiesData"][0]

SLACK_HOOK = "https://hooks.slack.test/services/T000/B000/XXXX"
SLACK_SECRET_ENV = "E2E_SLACK_HOOK"
FIVE_MINUTES = 300

RATIONALE = {
    "fit_summary": [
        "The notice asks for enterprise cloud migration, the company's primary line.",
        "NAICS 541512 is the company's primary code and the set-aside is satisfied.",
        "The company has migrated an Army mainframe estate before.",
    ],
    "matched_capabilities": ["Enterprise cloud migration", "NAICS 541512"],
    "gaps": [
        {
            "gap": "No FedRAMP High authorisation",
            "suggested_fix": "Team with an authorised CSP",
            "fix_type": "teaming",
        }
    ],
    "eligibility_risks": [{"risk": "SAM registration must stay active", "page": None}],
    "recommended_action": "pursue",
    "confidence": 0.82,
}


class StepClock:
    """A controlled clock anchored at the real start of the test: each read advances it
    by one second, so 'within five minutes of scoring' is a real, deterministic bound."""

    def __init__(self) -> None:
        self.start = datetime.now(UTC)
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return self.start + timedelta(seconds=self.reads)


@pytest.fixture(autouse=True)
def _slack_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tenant's Slack webhook lives behind `env:E2E_SLACK_HOOK` (M4-11 secret_ref)."""
    monkeypatch.setenv(SLACK_SECRET_ENV, SLACK_HOOK)


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        email_provider="memory",
        celery_task_always_eager=True,
        app_base_url="https://app.bidradar.test",
        api_base_url="https://api.bidradar.test",
    )


async def _seed(database: Database, embeddings: FakeEmbeddings):  # type: ignore[no-untyped-def]
    """A tenant whose profile genuinely fits the fixture notice (completeness >= 40)."""
    async with database.owner_session() as session:
        tenant, owner, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=Region.US,
            legal_name="Cloud Movers LLC",
            version=1,
            uei="CLOUD1234567",
            ein="12-3456789",
            website="https://cloudmovers.example",
            year_founded=2010,
            employee_count_total=140,
            target_countries=["US"],
            target_us_states=["NC", "VA"],
            target_cities=["Durham"],
            remote_ok=True,
            target_buyers=["ACC-APG RTP DIV"],
            notice_types_wanted=["presolicitation", "rfp", "combined"],
            value_min_usd=Decimal("100000"),
            value_max_usd=Decimal("50000000"),
            annual_revenue=[
                {"fiscal_year": 2024, "amount": "9000000.00", "currency": "USD"},
                {"fiscal_year": 2025, "amount": "11000000.00", "currency": "USD"},
            ],
        )
        session.add(profile)
        await session.flush()
        session.add_all(
            [
                ProfileCode(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    scheme=CodeScheme.NAICS,
                    code="541512",
                    is_primary=True,
                ),
                ProfileKeyword(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    kind=KeywordKind.INCLUDE,
                    term="cloud migration",
                    weight=Decimal("2.0"),
                ),
                ServiceLine(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    name="Enterprise cloud migration",
                    description=(
                        "Enterprise cloud migration and managed services: we migrate Army "
                        "and civilian enterprise estates to managed cloud platforms."
                    ),
                ),
                PastPerformance(
                    tenant_id=tenant.id,
                    profile_id=profile.id,
                    title="Army enterprise cloud migration",
                    customer="ACC-APG RTP DIV",
                    role=PerformanceRole.PRIME,
                    scope=(
                        "Enterprise cloud migration and managed services for an Army "
                        "command, including managed operations."
                    ),
                ),
                UserNotificationPrefs(tenant_id=tenant.id, user_id=owner.id, tz="America/New_York"),
                Integration(
                    tenant_id=tenant.id,
                    kind="slack",
                    enabled=True,
                    config={"channel": "#bids"},
                    secret_ref=f"env:{SLACK_SECRET_ENV}",
                ),
            ]
        )
        await session.flush()
        ids = (tenant.id, owner.id, owner.email, profile.id)
    async with database.session(ids[0]) as session:
        await index_profile(session, ids[3], embeddings=embeddings)
    return ids


def _pipeline(database: Database, embeddings: FakeEmbeddings, clock: StepClock):  # type: ignore[no-untyped-def]
    """The real bus with the real subscribers and the real channels behind it."""
    settings = _settings()
    bus = EventBus()
    install_opportunity_embeddings(settings, bus, embeddings=embeddings)
    llm = FakeLLM()
    llm.default_json = RATIONALE
    mail = MemoryProvider()
    channels = {
        "in_app": InAppChannel(),
        "email": EmailChannel(settings, provider=mail),
        # the real per-tenant resolvers: Slack reads the seeded `integrations` row and
        # its env: secret_ref, Teams finds nothing and every teams delivery is skipped
        "slack": SlackChannel(settings, resolver=slack_resolver(database)),
        "teams": TeamsChannel(settings, resolver=teams_resolver(database)),
    }
    dispatcher = Dispatcher(channels, settings, clock=clock)
    scorer = MatchScorer(
        settings=settings,
        database=database,
        embeddings=embeddings,
        rationale=RationaleGenerator(llm=llm, settings=settings, database=database),
        bus=bus,
        now=clock.start,
    )
    trigger = MatchTrigger(settings=settings, database=database, scorer=scorer, bus=bus).subscribe(
        bus
    )
    router = NotificationRouter(
        settings=settings, database=database, dispatcher=dispatcher, now=clock.start
    ).subscribe(bus)
    return settings, bus, trigger, router, mail, llm


async def _ingest(database: Database, bus: EventBus, record: dict, now: datetime):  # type: ignore[type-arg]
    notice = normalize_sam_notice(record)
    async with database.session(None) as session:
        result = await ingest(
            session, notice, raw_ref=f"raw/sam_opps/e2e/{uuid.uuid4().hex[:8]}", bus=bus, now=now
        )
        return result


@respx.mock
async def test_ingest_to_match_to_notify_end_to_end(
    database: Database, fake_embeddings: FakeEmbeddings, api_client: httpx.AsyncClient
) -> None:
    slack = respx.post(SLACK_HOOK).mock(return_value=httpx.Response(200, text="ok"))
    tenant_id, user_id, email, profile_id = await _seed(database, fake_embeddings)
    clock = StepClock()
    _settings_obj, bus, trigger, router, mail, llm = _pipeline(database, fake_embeddings, clock)

    result = await _ingest(database, bus, RECORD, clock.start)
    assert result.created
    opportunity_id = result.opportunity.id
    await trigger.drain()
    await router.drain()

    # --- the match ------------------------------------------------------------------
    async with database.session(tenant_id) as session:
        match = (await session.execute(select(Match))).scalar_one()
    assert match.profile_id == profile_id and match.opportunity_id == opportunity_id
    assert match.band == "high", f"scored {match.score} ({match.band}): {match.breakdown}"
    assert match.score >= Decimal(70)
    assert match.breakdown["signals"]["code_match"]["raw"] == 1.0
    # stage 3 ran through the FakeLLM and was cached on the row
    assert match.breakdown["rationale_status"] == "ok"
    assert match.rationale is not None
    assert match.rationale["recommended_action"] == "pursue"
    assert len(match.rationale["fit_summary"]) == 3
    assert len(llm.calls) == 1

    # --- the notification and its three channels --------------------------------------
    async with database.session(tenant_id) as session:
        notification = (await session.execute(select(Notification))).scalar_one()
        deliveries = (
            (
                await session.execute(
                    select(NotificationDelivery).order_by(NotificationDelivery.channel)
                )
            )
            .scalars()
            .all()
        )
    assert notification.user_id == user_id
    assert notification.event_type == Category.HIGH_FIT_MATCH.value
    assert notification.opportunity_id == opportunity_id
    assert notification.payload["band"] == "high"
    assert notification.payload["title"].startswith("Enterprise Cloud Migration")
    by_channel = {d.channel: d for d in deliveries}
    assert set(by_channel) == {"in_app", "email", "slack", "teams"}
    assert by_channel["in_app"].status == "sent"  # the bell item
    assert by_channel["email"].status == "sent"
    assert by_channel["slack"].status == "sent"
    assert by_channel["teams"].status == "skipped"  # no Teams webhook for this tenant

    # SPEC 7: within five minutes of scoring, on the controlled clock
    for channel in ("in_app", "email", "slack"):
        seconds = latency_seconds(notification, by_channel[channel])
        assert seconds is not None and 0 <= seconds < FIVE_MINUTES, channel

    # --- the email actually rendered ---------------------------------------------------
    assert len(mail.messages) == 1
    message = mail.messages[0]
    assert message.to == email
    assert "Enterprise Cloud Migration" in message.subject
    assert "Enterprise Cloud Migration" in message.html
    assert message.text and "https://app.bidradar.test" in message.text
    assert "List-Unsubscribe" in message.headers

    # --- the Slack post ------------------------------------------------------------------
    assert slack.called
    body = json.loads(slack.calls[0].request.content)
    assert body["channel"] == "#bids"
    assert any("Enterprise Cloud Migration" in json.dumps(block) for block in body["blocks"])

    # --- the API read path (SPEC 10.3: the detail carries the match) ---------------------
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.BID_MANAGER)
    detail = await api_client.get(f"/api/v1/opportunities/{opportunity_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    block = detail.json()["match"]
    assert block is not None
    assert block["band"] == "high" and block["score"] == float(match.score)
    assert block["profile_id"] == str(profile_id)
    assert block["breakdown"]["signals"]["code_match"]["raw"] == 1.0
    assert block["rationale"]["recommended_action"] == "pursue"
    listed = await api_client.get(
        "/api/v1/opportunities", params={"min_score": 70}, headers=headers
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["items"]] == [str(opportunity_id)]
    assert listed.json()["items"][0]["match"]["band"] == "high"

    # --- idempotency: the same record again changes and sends nothing ---------------------
    sent_before = len(mail.messages)
    slack_calls_before = len(slack.calls)
    again = await _ingest(database, bus, RECORD, clock.start + timedelta(minutes=1))
    assert again.unchanged
    await trigger.drain()
    await router.drain()
    async with database.session(tenant_id) as session:
        assert len((await session.execute(select(Notification))).scalars().all()) == 1
        assert len((await session.execute(select(NotificationDelivery))).scalars().all()) == 4
        assert len((await session.execute(select(Match))).scalars().all()) == 1
    assert len(mail.messages) == sent_before
    assert len(slack.calls) == slack_calls_before
    assert len(llm.calls) == 1  # the rationale cache held too


@respx.mock
async def test_a_tenant_that_does_not_fit_is_never_notified(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    """The same ingest, a profile that stage 1 drops: no match row and no notification."""
    respx.post(SLACK_HOOK).mock(return_value=httpx.Response(200))
    async with database.owner_session() as session:
        tenant, _owner, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=Region.US,
            legal_name="Lawn Care LLC",
            version=1,
            uei="LAWNS1234567",
            ein="98-7654321",
            website="https://lawns.example",
            year_founded=2015,
            employee_count_total=20,
            target_countries=["US"],
            # the fixture notice is a presolicitation, which this profile does not want
            notice_types_wanted=["grant"],
            annual_revenue=[{"fiscal_year": 2025, "amount": "1000000.00", "currency": "USD"}],
        )
        session.add(profile)
        await session.flush()
        session.add(
            ProfileCode(
                tenant_id=tenant.id,
                profile_id=profile.id,
                scheme=CodeScheme.NAICS,
                code="561730",
                is_primary=True,
            )
        )
        await session.flush()
        tenant_id = tenant.id
    clock = StepClock()
    _settings_obj, bus, trigger, router, mail, _llm = _pipeline(database, fake_embeddings, clock)
    await _ingest(database, bus, RECORD, clock.start)
    await trigger.drain()
    await router.drain()
    async with database.session(tenant_id) as session:
        assert (await session.execute(select(Match))).scalars().all() == []
        assert (await session.execute(select(Notification))).scalars().all() == []
    assert mail.messages == []


@respx.mock
async def test_an_amendment_on_the_same_notice_alerts_again(
    database: Database, fake_embeddings: FakeEmbeddings
) -> None:
    """A moved deadline is a new opportunity version, so it is a new match and a new alert."""
    respx.post(SLACK_HOOK).mock(return_value=httpx.Response(200))
    tenant_id, _user_id, _email, _profile_id = await _seed(database, fake_embeddings)
    clock = StepClock()
    _settings_obj, bus, trigger, router, mail, _llm = _pipeline(database, fake_embeddings, clock)
    await _ingest(database, bus, RECORD, clock.start)
    await trigger.drain()
    await router.drain()
    moved = {**RECORD, "responseDeadLine": "2026-11-20T14:00:00-05:00"}
    result = await _ingest(database, bus, moved, clock.start + timedelta(minutes=5))
    assert result.changed and result.version == 2
    await trigger.drain()
    await router.drain()
    async with database.session(tenant_id) as session:
        matches = (
            (await session.execute(select(Match).order_by(Match.opportunity_version)))
            .scalars()
            .all()
        )
        notifications = (await session.execute(select(Notification))).scalars().all()
    assert [m.opportunity_version for m in matches] == [1, 2]
    # SPEC 7's idempotency key is (user, event, object, version): a new version alerts
    assert len(notifications) == 2
    assert {n.version for n in notifications} == {1, 2}
    assert len(mail.messages) == 2
