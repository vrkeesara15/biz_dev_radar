"""M4-10: the email channel end to end — a real message in Mailpit, and the CAN-SPAM
per-category unsubscribe link that silences the next one."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.models import DeliveryStatus, Notification, Opportunity, UserNotificationPrefs
from app.notify.core import Dispatcher, NotificationEvent, Recipient, recipient_for
from app.notify.email import EmailChannel, MemoryProvider, SMTPProvider, build_message
from app.notify.unsubscribe import (
    ALL,
    apply_unsubscribe,
    is_unsubscribed,
    load_unsubscribed,
    resubscribe,
    sign_unsubscribe_token,
    unsubscribe_url,
    verify_unsubscribe_token,
)
from sqlalchemy import select

from tests.factories import create_tenant_with_owner

MAILPIT_URL = os.environ.get("MAILPIT_API_URL", "http://localhost:8025")
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)

PAYLOAD: dict[str, Any] = {
    "title": "Cloud migration services",
    "buyer": "Department of Energy",
    "value_amount": "1200000",
    "value_currency": "USD",
    "buyer_tz": "America/New_York",
    "score": 82.5,
    "band": "high",
    "rationale": {
        "fit_summary": ["NAICS 541511 exact", "Two similar DOE awards", "Remote delivery ok"],
        "gaps": [{"gap": "No FedRAMP Moderate", "suggested_fix": "Team with an authorised CSP"}],
    },
}


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def _seed(database: Database) -> tuple[uuid.UUID, uuid.UUID, str, uuid.UUID]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        opp = Opportunity(
            source_id="sam_opps",
            external_id=f"n-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Cloud migration services",
            response_due_at=DUE,
            source_tz="America/New_York",
            version=2,
        )
        session.add(opp)
        await session.flush()
        return tenant.id, user.id, user.email, opp.id


def _event(tenant_id: uuid.UUID, opp_id: uuid.UUID, **overrides: Any) -> NotificationEvent:
    values: dict[str, Any] = {
        "event_type": "high_fit_match",
        "tenant_id": tenant_id,
        "opportunity_id": opp_id,
        "version": 2,
        "payload": dict(PAYLOAD),
        "occurred_at": T0,
        "response_due_at": DUE,
    }
    values.update(overrides)
    return NotificationEvent(**values)


# --- Mailpit --------------------------------------------------------------------------------


@pytest.fixture()
async def mailpit():  # type: ignore[no-untyped-def]
    """Mailpit's HTTP API, emptied before the test; skips when the container is not up."""
    async with httpx.AsyncClient(base_url=MAILPIT_URL, timeout=5.0) as client:
        try:
            await client.get("/api/v1/info")
        except httpx.HTTPError as exc:
            pytest.skip(f"Mailpit is not reachable at {MAILPIT_URL} ({exc})")
        await client.delete("/api/v1/messages")
        yield client
        await client.delete("/api/v1/messages")


@pytest.mark.mailpit
async def test_email_arrives_in_mailpit_with_the_spec_7_content(
    mailpit: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    mail_settings = settings.model_copy(
        update={"email_from": "alerts@bidradar.test", "email_from_name": "BidRadar"}
    )
    channel = EmailChannel(mail_settings, provider=SMTPProvider("localhost", 1025), now=lambda: T0)
    clock = FakeClock()
    dispatcher = Dispatcher({"email": channel}, mail_settings, clock=clock, sleep=clock.sleep)
    async with database.session(tenant_id) as session:
        result = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id),
            [
                Recipient(
                    user_id=user_id,
                    channels=("email",),
                    email=email,
                    name="Ada Lovelace",
                    tz="Asia/Kolkata",
                )
            ],
        )
    assert [d.status for d in result.deliveries] == [DeliveryStatus.SENT.value]

    listing = (await mailpit.get("/api/v1/messages", params={"limit": 10})).json()
    assert listing["total"] == 1
    summary = listing["messages"][0]
    assert summary["To"][0]["Address"] == email
    assert summary["Subject"] == "High fit 82.5: Cloud migration services"
    message = (await mailpit.get(f"/api/v1/message/{summary['ID']}")).json()
    for part in (message["HTML"], message["Text"]):
        assert "Cloud migration services" in part
        assert "Department of Energy" in part
        assert "$1,200,000" in part
        assert "Oct 14, 2:00 PM EDT" in part and "11:30 PM IST" in part  # user's own zone
        assert "3d 6h" in part  # countdown
        assert "82.5" in part
        assert "NAICS 541511 exact" in part
        assert "No FedRAMP Moderate" in part  # top gap
        assert f"/app/opportunities/{opp_id}" in part  # deep link
        assert "/api/v1/notifications/actions/" in part  # one-click actions
        assert "/api/v1/notifications/unsubscribe/" in part
        assert mail_settings.email_postal_address in part
    headers = (await mailpit.get(f"/api/v1/message/{summary['ID']}/headers")).json()
    assert headers["List-Unsubscribe-Post"] == ["List-Unsubscribe=One-Click"]
    assert headers["List-Unsubscribe"][0].startswith(f"<{settings.api_base_url}")
    assert headers["X-Bidradar-Event"] == ["high_fit_match"]


# --- unsubscribe ------------------------------------------------------------------------------


async def test_unsubscribe_link_from_the_email_silences_that_category(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, email, opp_id = await _seed(database)
    provider = MemoryProvider()
    channel = EmailChannel(settings, provider=provider, now=lambda: T0)
    clock = FakeClock()
    dispatcher = Dispatcher({"email": channel}, settings, clock=clock, sleep=clock.sleep)
    recipient = Recipient(user_id=user_id, channels=("email",), email=email, tz="America/New_York")
    async with database.session(tenant_id) as session:
        await dispatcher.dispatch(session, _event(tenant_id, opp_id), [recipient])
    assert len(provider.messages) == 1

    link = provider.messages[0].headers["List-Unsubscribe"].split(">", 1)[0].lstrip("<")
    response = await api_client.get(link.removeprefix(settings.api_base_url))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "high_fit_match"
    assert body["unsubscribed_categories"] == ["high_fit_match"]
    assert body["redirect"].endswith("/app/settings/notifications")

    async with database.session(tenant_id) as session:
        assert await load_unsubscribed(session, user_id) == ("high_fit_match",)
        # the next high-fit match for the same user is skipped, not retried, not failed
        stored = (await session.execute(select(Notification))).scalars().one()
        rebuilt = await recipient_for(session, stored, ("email",))
        assert rebuilt.unsubscribed == ("high_fit_match",)
        result = await dispatcher.dispatch(session, _event(tenant_id, opp_id, version=3), [rebuilt])
    assert [d.status for d in result.deliveries] == [DeliveryStatus.SKIPPED.value]
    assert result.deliveries[0].attempts == 1
    assert len(provider.messages) == 1  # nothing new went out

    # a digest still reaches them: only the one category was silenced
    async with database.session(tenant_id) as session:
        digest = await dispatcher.dispatch(
            session,
            _event(tenant_id, opp_id, event_type="digest", version=1, payload={"items": []}),
            [rebuilt],
        )
    assert [d.status for d in digest.deliveries] == [DeliveryStatus.SENT.value]


async def test_one_click_post_unsubscribes_from_all_and_is_idempotent(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, _, _ = await _seed(database)
    url = unsubscribe_url(settings, tenant_id=tenant_id, user_id=user_id, category=ALL)
    path = url.removeprefix(settings.api_base_url)
    first = await api_client.post(path)
    assert first.status_code == 200 and first.json()["unsubscribed_categories"] == [ALL]
    second = await api_client.post(path)
    assert second.json()["unsubscribed_categories"] == [ALL]
    async with database.session(tenant_id) as session:
        assert await load_unsubscribed(session, user_id) == (ALL,)
        assert is_unsubscribed("digest", await load_unsubscribed(session, user_id))


async def test_all_supersedes_and_clears_single_categories(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, _, _ = await _seed(database)
    for category in ("high_fit_match", "digest"):
        url = unsubscribe_url(settings, tenant_id=tenant_id, user_id=user_id, category=category)
        await api_client.get(url.removeprefix(settings.api_base_url))
    async with database.session(tenant_id) as session:
        assert await load_unsubscribed(session, user_id) == ("high_fit_match", "digest")
    all_url = unsubscribe_url(settings, tenant_id=tenant_id, user_id=user_id, category=ALL)
    await api_client.get(all_url.removeprefix(settings.api_base_url))
    async with database.session(tenant_id) as session:
        assert await load_unsubscribed(session, user_id) == (ALL,)
        # a later single-category opt-out cannot narrow "all"
        claims = verify_unsubscribe_token(
            sign_unsubscribe_token(
                settings, tenant_id=tenant_id, user_id=user_id, category="amendment"
            ),
            settings.auth_secret,
        )
        assert await apply_unsubscribe(session, claims) == (ALL,)
        assert await resubscribe(session, user_id, ALL) == ()
        assert await resubscribe(session, uuid.uuid4(), ALL) == ()


async def test_unsubscribe_creates_the_prefs_row_and_keeps_existing_settings(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, _, _ = await _seed(database)
    async with database.session(tenant_id) as session:
        assert await load_unsubscribed(session, user_id) == ()  # no row yet
    url = unsubscribe_url(settings, tenant_id=tenant_id, user_id=user_id, category="digest")
    await api_client.get(url.removeprefix(settings.api_base_url))
    async with database.session(tenant_id) as session:
        row = (
            await session.execute(
                select(UserNotificationPrefs).where(UserNotificationPrefs.user_id == user_id)
            )
        ).scalar_one()
        assert row.unsubscribed_categories == ["digest"]
        assert row.tz == "UTC" and row.digest_time == "08:00"  # defaults untouched
        assert await resubscribe(session, user_id, "digest") == ()


async def test_tampered_foreign_and_unknown_category_tokens_are_rejected(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id, _, _ = await _seed(database)
    good = unsubscribe_url(settings, tenant_id=tenant_id, user_id=user_id, category="digest")
    path = good.removeprefix(settings.api_base_url)
    assert (await api_client.get(path + "x")).status_code == 401
    assert (await api_client.post(path + "x")).status_code == 401

    other = Settings(_env_file=None, auth_secret="another-secret-0123456789abcdef0123456789")  # type: ignore[call-arg]
    forged = unsubscribe_url(other, tenant_id=tenant_id, user_id=user_id, category="digest")
    assert (await api_client.get(forged.removeprefix(other.api_base_url))).status_code == 401

    with pytest.raises(Exception, match="invalid or expired"):
        verify_unsubscribe_token(
            sign_unsubscribe_token(
                settings, tenant_id=tenant_id, user_id=user_id, category="not_a_category"
            ),
            settings.auth_secret,
        )
    expired = sign_unsubscribe_token(
        settings,
        tenant_id=tenant_id,
        user_id=user_id,
        category="digest",
        now=datetime.now(UTC) - timedelta(seconds=settings.notify_action_ttl_seconds + 10),
    )
    assert (await api_client.get(f"/api/v1/notifications/unsubscribe/{expired}")).status_code == 401
    async with database.session(tenant_id) as session:
        assert await load_unsubscribed(session, user_id) == ()


async def test_message_build_is_tenant_scoped(database: Database, settings: Settings) -> None:
    """A token minted for tenant A's user never lands on tenant B's prefs row."""
    tenant_a, user_a, _, _ = await _seed(database)
    tenant_b, user_b, _, _ = await _seed(database)
    claims = verify_unsubscribe_token(
        sign_unsubscribe_token(settings, tenant_id=tenant_a, user_id=user_a, category="digest"),
        settings.auth_secret,
    )
    async with database.session(tenant_a) as session:
        await apply_unsubscribe(session, claims)
    async with database.session(tenant_b) as session:
        assert await load_unsubscribed(session, user_b) == ()
        assert await load_unsubscribed(session, user_a) == ()  # RLS hides A's row from B


def test_build_message_uses_the_tenant_and_user_from_the_notification(
    settings: Settings,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    notification = Notification(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        event_type="digest",
        version=1,
        idempotency_key="k",
        payload={"items": [{"title": "One", "link": "https://x/1"}]},
    )
    message = build_message(notification, settings=settings, to="a@example.com", now=T0)
    token = message.headers["List-Unsubscribe"].split(">", 1)[0].rsplit("/", 1)[-1]
    claims = verify_unsubscribe_token(token, settings.auth_secret, now=T0)
    assert (claims.tenant_id, claims.user_id, claims.category) == (tenant_id, user_id, "digest")
