"""WhatsApp Business channel (SPEC 7, 12; M6-05): gates, templates, BSPs, receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from app.core.config import Settings
from app.models import Notification
from app.models.notifications import DeliveryStatus
from app.notify.core import Recipient
from app.notify.whatsapp import (
    ALLOWED_EVENTS,
    GupshupProvider,
    TwilioProvider,
    WhatsAppChannel,
    WhatsAppSendError,
    WhatsAppSignatureError,
    WhatsAppTarget,
    build_provider,
    is_e164,
    parse_receipt,
    template_for,
    template_variables,
    verify_receipt_signature,
)

DUE = datetime.now(UTC) + timedelta(days=3, hours=4)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "whatsapp_provider": "gupshup",
        "whatsapp_template_deadline": "bidradar_deadline_v1",
        "whatsapp_template_high_match": "bidradar_high_fit_v1",
        "gupshup_api_key": "gs-key",
        "gupshup_source_number": "918000000000",
        "gupshup_app_name": "BidRadarIN",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _notification(event_type: str = "deadline_reminder", **payload: Any) -> Notification:
    body: dict[str, Any] = {
        "title": "Supply of   laptops to NIC",
        "buyer": "National Informatics Centre",
        "deep_link": "http://localhost:3000/app/pursuits/1",
        "buyer_tz": "Asia/Kolkata",
        "due_at": DUE.isoformat(),
    }
    body.update(payload)
    return Notification(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        event_type=event_type,
        idempotency_key=f"k-{uuid.uuid4()}",
        payload=body,
    )


def _recipient(**overrides: Any) -> Recipient:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "channels": ("whatsapp",),
        "tz": "Asia/Kolkata",
    }
    values.update(overrides)
    return Recipient(**values)


def _eligible(target: WhatsAppTarget):  # type: ignore[no-untyped-def]
    async def resolve(tenant_id: uuid.UUID, user_id: uuid.UUID) -> WhatsAppTarget:
        return target

    return resolve


IN_VERIFIED = WhatsAppTarget(phone_e164="+919876543210", phone_verified=True, region="in")


def test_spec_7_limits_whatsapp_to_two_events() -> None:
    assert ALLOWED_EVENTS == ("deadline_reminder", "high_fit_match")
    settings = _settings()
    assert template_for(settings, "deadline_reminder") == "bidradar_deadline_v1"
    assert template_for(settings, "high_fit_match") == "bidradar_high_fit_v1"
    assert template_for(settings, "amendment") is None
    assert template_for(_settings(whatsapp_template_deadline=""), "deadline_reminder") is None


def test_e164_validation() -> None:
    assert is_e164("+919876543210")
    assert not is_e164("9876543210")
    assert not is_e164("+0123456789")
    assert not is_e164(None)
    assert not is_e164("+1234")


def test_template_variables_are_single_line_and_clipped() -> None:
    variables = template_variables(_notification(), _recipient())
    assert variables[0] == "Supply of laptops to NIC"  # whitespace collapsed
    assert variables[1] == "National Informatics Centre"
    assert "IST" in variables[2]
    assert variables[2].endswith(")")  # the countdown
    assert variables[3] == "http://localhost:3000/app/pursuits/1"
    assert all("\n" not in v and len(v) <= 200 for v in variables)

    high = template_variables(_notification("high_fit_match", score=82), _recipient())
    assert high[2] == "82 / 100"

    bare = template_variables(
        _notification(title=None, buyer=None, deep_link=None, due_at=None), _recipient()
    )
    assert bare == ["a tracked opportunity", "-", "-", "-"]
    unparseable = template_variables(_notification(due_at="not a date"), _recipient())
    assert unparseable[2] == "-"
    long_title = template_variables(_notification(title="x" * 400), _recipient())
    assert len(long_title[0]) == 200


def test_the_three_gates_skip_before_any_network_call() -> None:
    channel = WhatsAppChannel(_settings(), provider=object(), eligibility=_eligible(IN_VERIFIED))
    import asyncio

    async def run(notification: Notification, target: WhatsAppTarget, settings: Settings):  # type: ignore[no-untyped-def]
        ch = WhatsAppChannel(settings, provider=object(), eligibility=_eligible(target))
        return await ch.send(None, notification, _recipient())

    # 1. the wrong event
    result = asyncio.run(run(_notification("amendment"), IN_VERIFIED, _settings()))
    assert result.skipped and "does not carry amendment" in (result.error or "")
    # 2. a US tenant
    us = WhatsAppTarget(phone_e164="+12025550123", phone_verified=True, region="us")
    result = asyncio.run(run(_notification(), us, _settings()))
    assert result.skipped and "Indian tenant" in (result.error or "")
    # 3. an unverified number
    unverified = WhatsAppTarget(phone_e164="+919876543210", phone_verified=False, region="in")
    assert asyncio.run(run(_notification(), unverified, _settings())).skipped
    # 4. no template configured
    result = asyncio.run(
        run(_notification(), IN_VERIFIED, _settings(whatsapp_template_deadline=""))
    )
    assert result.skipped and "no approved whatsapp template" in (result.error or "")
    # 5. no provider at all
    off = WhatsAppChannel(_settings(whatsapp_provider=""), eligibility=_eligible(IN_VERIFIED))
    assert off.provider is None
    assert channel.provider is not None


async def test_gupshup_sends_the_approved_template() -> None:
    settings = _settings()
    async with httpx.AsyncClient() as client, respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.gupshup.io/wa/api/v1/template/msg").mock(
            return_value=httpx.Response(200, json={"status": "submitted", "messageId": "gs-1"})
        )
        channel = WhatsAppChannel(
            settings,
            provider=GupshupProvider(settings, client=client),
            eligibility=_eligible(IN_VERIFIED),
        )
        result = await channel.send(None, _notification(), _recipient())
    assert result.ok
    assert result.provider_ref == "gs-1"
    request = route.calls[0].request
    assert request.headers["apikey"] == "gs-key"
    form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
    assert form["destination"] == "919876543210"
    assert form["source"] == "918000000000"
    from urllib.parse import unquote_plus

    template = json.loads(unquote_plus(form["template"]))
    assert template["id"] == "bidradar_deadline_v1"
    assert len(template["params"]) == 4


async def test_twilio_sends_with_basic_auth_and_content_variables() -> None:
    settings = _settings(
        whatsapp_provider="twilio",
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_whatsapp_from="+14155238886",
    )
    url = "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    async with httpx.AsyncClient() as client, respx.mock(assert_all_called=True) as mock:
        route = mock.post(url).mock(return_value=httpx.Response(201, json={"sid": "SM1"}))
        channel = WhatsAppChannel(
            settings,
            provider=TwilioProvider(settings, client=client),
            eligibility=_eligible(IN_VERIFIED),
        )
        result = await channel.send(None, _notification("high_fit_match", score=91), _recipient())
    assert result.ok and result.provider_ref == "SM1"
    request = route.calls[0].request
    assert request.headers["authorization"].startswith("Basic ")
    from urllib.parse import parse_qs

    form = parse_qs(request.content.decode())
    assert form["From"] == ["whatsapp:+14155238886"]
    assert form["To"] == ["whatsapp:+919876543210"]
    assert form["ContentSid"] == ["bidradar_high_fit_v1"]
    assert json.loads(form["ContentVariables"][0])["3"] == "91 / 100"


async def test_a_provider_error_is_a_failed_send_not_an_exception() -> None:
    settings = _settings()
    async with httpx.AsyncClient() as client, respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.gupshup.io/wa/api/v1/template/msg").mock(
            return_value=httpx.Response(429, text="rate limited")
        )
        channel = WhatsAppChannel(
            settings,
            provider=GupshupProvider(settings, client=client),
            eligibility=_eligible(IN_VERIFIED),
        )
        result = await channel.send(None, _notification(), _recipient())
    assert not result.ok and not result.skipped
    assert "429" in (result.error or "")

    async with httpx.AsyncClient() as client, respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.gupshup.io/wa/api/v1/template/msg").mock(
            side_effect=httpx.ConnectTimeout("boom")
        )
        channel = WhatsAppChannel(
            settings,
            provider=GupshupProvider(settings, client=client),
            eligibility=_eligible(IN_VERIFIED),
        )
        result = await channel.send(None, _notification(), _recipient())
    assert "ConnectTimeout" in (result.error or "")

    # a BSP that answers 200 with no id is still a failure
    async with httpx.AsyncClient() as client, respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.gupshup.io/wa/api/v1/template/msg").mock(
            return_value=httpx.Response(200, json={"status": "submitted"})
        )
        with pytest.raises(WhatsAppSendError, match="no message id"):
            await GupshupProvider(settings, client=client).send_template(
                __import__("app.notify.whatsapp", fromlist=["TemplateMessage"]).TemplateMessage(
                    "t", "+919876543210", ["a"]
                )
            )


def test_build_provider_needs_complete_credentials() -> None:
    assert isinstance(build_provider(_settings()), GupshupProvider)
    assert build_provider(_settings(gupshup_api_key="")) is None
    assert build_provider(_settings(gupshup_source_number="")) is None
    twilio = _settings(
        whatsapp_provider="twilio",
        twilio_account_sid="AC",
        twilio_auth_token="t",
        twilio_whatsapp_from="+1",
    )
    assert isinstance(build_provider(twilio), TwilioProvider)
    assert build_provider(_settings(whatsapp_provider="twilio")) is None
    assert build_provider(_settings(whatsapp_provider="")) is None
    assert build_provider(_settings(whatsapp_provider="meta")) is None


@pytest.mark.parametrize(
    ("provider", "payload", "expected"),
    [
        (
            "twilio",
            {"MessageSid": "SM1", "MessageStatus": "delivered"},
            (DeliveryStatus.SENT.value, None),
        ),
        ("twilio", {"MessageSid": "SM1", "MessageStatus": "read"}, ("opened", None)),
        (
            "twilio",
            {"MessageSid": "SM1", "MessageStatus": "failed", "ErrorCode": "63016"},
            ("failed", "63016"),
        ),
        ("twilio", {"SmsSid": "SM2", "SmsStatus": "queued"}, ("sent", None)),
        ("gupshup", {"type": "delivered", "payload": {"gsId": "gs-1"}}, ("sent", None)),
        (
            "gupshup",
            {"type": "failed", "payload": {"gsId": "gs-1", "payload": {"reason": "blocked"}}},
            ("failed", "blocked"),
        ),
        ("gupshup", {"type": "read", "payload": {"id": "gs-2"}}, ("opened", None)),
    ],
)
def test_receipts_map_to_delivery_statuses(
    provider: str, payload: dict[str, Any], expected: tuple[str, str | None]
) -> None:
    receipt = parse_receipt(provider, payload)
    assert receipt is not None
    assert (receipt.status, receipt.error) == expected


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("twilio", {"MessageStatus": "delivered"}),  # no id
        ("twilio", {"MessageSid": "SM1", "MessageStatus": "receiving"}),  # unknown status
        ("gupshup", {"type": "message", "payload": {"gsId": "gs-1"}}),  # an inbound reply
        ("meta", {"anything": True}),  # unknown provider
    ],
)
def test_untracked_receipts_are_ignored(provider: str, payload: dict[str, Any]) -> None:
    assert parse_receipt(provider, payload) is None


def test_receipt_signature_is_verified_when_a_secret_is_configured() -> None:
    body = b'{"type":"delivered"}'
    secret = "bsp-secret"
    settings = _settings(whatsapp_webhook_secret=secret)
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    verify_receipt_signature(
        "gupshup", settings, headers={"x-gupshup-signature": digest}, body=body
    )
    with pytest.raises(WhatsAppSignatureError):
        verify_receipt_signature("gupshup", settings, headers={}, body=body)
    with pytest.raises(WhatsAppSignatureError):
        verify_receipt_signature(
            "gupshup", settings, headers={"x-gupshup-signature": "deadbeef"}, body=body
        )
    with pytest.raises(WhatsAppSignatureError):  # a tampered body
        verify_receipt_signature(
            "gupshup", settings, headers={"x-gupshup-signature": digest}, body=body + b" "
        )
    # Twilio's receipt uses its own header
    twilio_digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    verify_receipt_signature(
        "twilio", settings, headers={"x-twilio-signature": twilio_digest}, body=body
    )
    # no secret configured: receipts are accepted unsigned (documented)
    verify_receipt_signature("gupshup", _settings(), headers={}, body=body)
