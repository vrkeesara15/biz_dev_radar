"""M4-12: the Teams Adaptive Card, the in-app channel and the web-push fan-out."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from app.core.config import Settings
from app.models import Notification
from app.notify.core import Recipient
from app.notify.in_app import InAppChannel
from app.notify.push import (
    PushChannel,
    PushResult,
    Subscription,
    WebPushSender,
    build_payload,
)
from app.notify.teams import TeamsChannel, TeamsSettings, build_card

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
HOOK = "https://acme.webhook.office.com/webhookb2/abc/IncomingWebhook/def"

ACTIONS = {
    "pursue": "https://api.example/api/v1/notifications/actions/tok-pursue",
    "watch": "https://api.example/api/v1/notifications/actions/tok-watch",
    "pass": "https://api.example/api/v1/notifications/actions/tok-pass",
    "assign": "https://api.example/api/v1/notifications/actions/tok-assign",
}


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _notification(**overrides: Any) -> Notification:
    values: dict[str, Any] = {
        "id": uuid.UUID("33333333-3333-4333-8333-333333333333"),
        "tenant_id": TENANT,
        "user_id": USER,
        "event_type": "high_fit_match",
        "version": 2,
        "idempotency_key": "k",
        "payload": {
            "title": "Cloud migration services",
            "buyer": "Department of Energy",
            "value_amount": "1200000",
            "value_currency": "USD",
            "response_due_at": DUE.isoformat(),
            "buyer_tz": "America/New_York",
            "score": 82.5,
            "band": "high",
            "top_gap": "No FedRAMP Moderate",
            "deep_link": "https://app.example/app/opportunities/abc",
            "actions": dict(ACTIONS),
            "rationale": {"fit_summary": ["NAICS exact", "Two DOE awards", "Remote ok", "extra"]},
        },
    }
    values.update(overrides)
    return Notification(**values)


# --- Teams -------------------------------------------------------------------------------------


def test_adaptive_card_shape_and_actions() -> None:
    envelope = build_card(_notification(), now=NOW)
    assert envelope["type"] == "message"
    attachment = envelope["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    serialized = json.dumps(card)
    assert "Cloud migration services" in serialized
    assert "Oct 14, 2:00 PM EDT" in serialized and "3d 6h" in serialized
    assert "No FedRAMP Moderate" in serialized

    facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
    assert [f["title"] for f in facts] == ["Buyer", "Value", "Response due", "Fit"]
    assert facts[1]["value"] == "$1,200,000"

    bullets = [b for b in card["body"] if b["type"] == "TextBlock" and b["text"].startswith("- ")]
    assert bullets[0]["text"].count("- ") == 3 and "extra" not in bullets[0]["text"]

    assert [a["title"] for a in card["actions"]] == [
        "Pursue",
        "Watch",
        "Pass",
        "Assign",
        "Open",
    ]
    assert all(a["type"] == "Action.OpenUrl" for a in card["actions"])
    assert card["actions"][0]["url"] == ACTIONS["pursue"]
    assert card["actions"][-1]["url"] == "https://app.example/app/opportunities/abc"


def test_adaptive_card_without_payload_details() -> None:
    card = build_card(
        _notification(event_type="adapter_failing", payload={"source_id": "cppp"}), now=NOW
    )["attachments"][0]["content"]
    assert "actions" not in card
    assert [b["type"] for b in card["body"]] == ["TextBlock", "TextBlock"]
    assert card["body"][0]["text"] == "Adapter Failing"


@respx.mock
async def test_teams_channel_posts_and_reports_failures() -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200, text="1"))

    async def resolver(tenant_id: uuid.UUID) -> TeamsSettings:
        assert tenant_id == TENANT
        return TeamsSettings(HOOK, {}, True)

    async with httpx.AsyncClient() as client:
        channel = TeamsChannel(_settings(), resolver=resolver, client=client, now=lambda: NOW)
        assert (await channel.send(None, _notification(), Recipient(USER, ("teams",)))).ok
    assert json.loads(route.calls.last.request.content)["type"] == "message"

    respx.post(HOOK).mock(return_value=httpx.Response(429, text="throttled"))
    async with httpx.AsyncClient() as client:
        failed = await TeamsChannel(_settings(), webhook_url=HOOK, client=client).send(
            None, _notification(), Recipient(USER, ("teams",))
        )
    assert not failed.ok and failed.error is not None and "HTTP 429" in failed.error

    respx.post(HOOK).mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as client:
        down = await TeamsChannel(_settings(), webhook_url=HOOK, client=client).send(
            None, _notification(), Recipient(USER, ("teams",))
        )
    assert not down.ok and down.error is not None and "ReadTimeout" in down.error


async def test_teams_channel_skips_when_unconfigured() -> None:
    assert (
        await TeamsChannel(_settings()).send(None, _notification(), Recipient(USER, ("teams",)))
    ).skipped

    async def disabled(tenant_id: uuid.UUID) -> TeamsSettings:
        return TeamsSettings(HOOK, {}, enabled=False)

    assert (
        await TeamsChannel(_settings(), resolver=disabled).send(
            None, _notification(), Recipient(USER, ("teams",))
        )
    ).skipped


@respx.mock
async def test_teams_channel_owns_its_client_when_none_is_injected() -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(200))
    channel = TeamsChannel(_settings(), webhook_url=HOOK)
    assert (await channel.send(None, _notification(), Recipient(USER, ("teams",)))).ok


# --- in-app ------------------------------------------------------------------------------------


async def test_in_app_channel_confirms_the_row() -> None:
    notification = _notification()
    result = await InAppChannel().send(None, notification, Recipient(USER, ("in_app",)))
    assert result.ok and result.provider_ref == str(notification.id)


# --- push --------------------------------------------------------------------------------------


SUBS = (
    Subscription("https://fcm.googleapis.com/fcm/send/aaa", "p-a", "a-a"),
    Subscription("https://updates.push.services.mozilla.com/wpush/v2/bbb", "p-b", "a-b"),
)


class FakeSender:
    def __init__(self, *results: PushResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str]] = []

    async def send(self, subscription: Subscription, payload: str) -> PushResult:
        self.calls.append((subscription.endpoint, payload))
        return self.results.pop(0) if self.results else PushResult(True)


def _resolver(*subs: Subscription):  # type: ignore[no-untyped-def]
    async def resolve(tenant_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Subscription, ...]:
        assert (tenant_id, user_id) == (TENANT, USER)
        return subs

    return resolve


def test_push_payload_is_what_the_service_worker_needs() -> None:
    message = json.loads(build_payload(_notification()))
    assert message == {
        "title": "Cloud migration services",
        "body": "Department of Energy · fit 82.5",
        "tag": str(_notification().id),
        "event_type": "high_fit_match",
        "url": "https://app.example/app/opportunities/abc",
        "notification_id": str(_notification().id),
    }
    bare = json.loads(build_payload(_notification(payload={})))
    assert bare["title"] == "High Fit Match" and bare["body"] == "high fit match"
    assert bare["url"] is None


async def test_push_fans_out_to_every_subscription() -> None:
    sender = FakeSender()
    channel = PushChannel(_settings(), subscriptions=_resolver(*SUBS), sender=sender)
    result = await channel.send(None, _notification(), Recipient(USER, ("push",)))
    assert result.ok and result.provider_ref == "2/2"
    assert [c[0] for c in sender.calls] == [s.endpoint for s in SUBS]
    assert json.loads(sender.calls[0][1])["title"] == "Cloud migration services"


async def test_push_prunes_gone_subscriptions_and_still_counts_the_live_one() -> None:
    pruned: list[str] = []

    async def on_gone(tenant_id: uuid.UUID, subscription: Subscription) -> None:
        assert tenant_id == TENANT
        pruned.append(subscription.endpoint)

    sender = FakeSender(PushResult(False, gone=True, error="410"), PushResult(True))
    channel = PushChannel(
        _settings(), subscriptions=_resolver(*SUBS), sender=sender, on_gone=on_gone
    )
    result = await channel.send(None, _notification(), Recipient(USER, ("push",)))
    assert result.ok and result.provider_ref == "1/2"
    assert pruned == [SUBS[0].endpoint]


async def test_push_reports_a_real_failure_so_the_dispatcher_retries() -> None:
    sender = FakeSender(PushResult(False, error="push: 503 unavailable"))
    channel = PushChannel(_settings(), subscriptions=_resolver(SUBS[0]), sender=sender)
    result = await channel.send(None, _notification(), Recipient(USER, ("push",)))
    assert not result.ok and not result.skipped
    assert result.error is not None and "503" in result.error


async def test_push_skips_without_subscriptions_or_resolver() -> None:
    assert (
        await PushChannel(_settings()).send(None, _notification(), Recipient(USER, ("push",)))
    ).skipped
    empty = PushChannel(_settings(), subscriptions=_resolver(), sender=FakeSender())
    assert (await empty.send(None, _notification(), Recipient(USER, ("push",)))).skipped
    all_gone = PushChannel(
        _settings(),
        subscriptions=_resolver(SUBS[0]),
        sender=FakeSender(PushResult(False, gone=True)),
    )
    result = await all_gone.send(None, _notification(), Recipient(USER, ("push",)))
    assert result.skipped and result.error == "every push subscription is gone"


async def test_webpush_sender_needs_a_vapid_key() -> None:
    result = await WebPushSender(_settings()).send(SUBS[0], "{}")
    assert not result.ok and result.error is not None and "VAPID_PRIVATE_KEY" in result.error


def test_webpush_sender_maps_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import pywebpush

    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    def gone(**kwargs: Any) -> None:
        exc = pywebpush.WebPushException("subscription gone")
        exc.response = Response(410)  # type: ignore[attr-defined]
        raise exc

    sender = WebPushSender(_settings(vapid_private_key="dummy"))
    monkeypatch.setattr(pywebpush, "webpush", gone)
    result = sender._send_sync(SUBS[0], "{}")
    assert not result.ok and result.gone

    def throttled(**kwargs: Any) -> None:
        exc = pywebpush.WebPushException("too many")
        exc.response = Response(429)  # type: ignore[attr-defined]
        raise exc

    monkeypatch.setattr(pywebpush, "webpush", throttled)
    assert not sender._send_sync(SUBS[0], "{}").gone

    def exploded(**kwargs: Any) -> None:
        raise ConnectionError("dns")

    monkeypatch.setattr(pywebpush, "webpush", exploded)
    broken = sender._send_sync(SUBS[0], "{}")
    assert not broken.ok and broken.error is not None and "ConnectionError" in broken.error

    monkeypatch.setattr(pywebpush, "webpush", lambda **kwargs: None)
    assert sender._send_sync(SUBS[0], "{}").ok


def test_subscription_info_is_the_browser_shape() -> None:
    assert SUBS[0].as_info() == {
        "endpoint": SUBS[0].endpoint,
        "keys": {"p256dh": "p-a", "auth": "a-a"},
    }
