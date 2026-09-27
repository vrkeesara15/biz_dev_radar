"""M4-10: EmailProvider implementations (SES / SendGrid / SMTP / memory), the MIME build
and the EmailChannel's CAN-SPAM skip."""

from __future__ import annotations

import smtplib
import uuid
from datetime import UTC, datetime
from email import message_from_bytes
from typing import Any

import httpx
import pytest
import respx
from app.core.config import EmailProviderName, Region, Settings
from app.models import Notification
from app.notify.core import Recipient
from app.notify.email import (
    EmailChannel,
    MemoryProvider,
    OutboundEmail,
    SendGridProvider,
    SESProvider,
    SMTPProvider,
    build_email_provider,
    build_message,
    ses_region_for,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _message(**overrides: Any) -> OutboundEmail:
    values: dict[str, Any] = {
        "to": "buyer@example.com",
        "to_name": "Ada Lovelace",
        "subject": "High fit 82.5: Cloud migration services",
        "html": "<p>hello</p>",
        "text": "hello",
        "from_email": "alerts@bidradar.example",
        "from_name": "BidRadar",
        "headers": {"List-Unsubscribe": "<https://api/u/1>", "X-BidRadar-Event": "high_fit_match"},
        "category": "high_fit_match",
    }
    values.update(overrides)
    return OutboundEmail(**values)


def _notification(**overrides: Any) -> Notification:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "user_id": USER,
        "event_type": "high_fit_match",
        "version": 1,
        "idempotency_key": "k",
        "payload": {"title": "Cloud migration services", "score": 82.5, "band": "high"},
    }
    values.update(overrides)
    return Notification(**values)


# --- MIME ------------------------------------------------------------------------------------


def test_mime_is_multipart_alternative_with_the_unsubscribe_headers() -> None:
    mime = _message().as_mime(message_id="<fixed@bidradar>")
    parsed = message_from_bytes(mime.as_bytes())
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed["To"] == "Ada Lovelace <buyer@example.com>"
    assert parsed["From"] == "BidRadar <alerts@bidradar.example>"
    assert parsed["Message-ID"] == "<fixed@bidradar>"
    assert parsed["List-Unsubscribe"] == "<https://api/u/1>"
    types = [part.get_content_type() for part in parsed.walk()][1:]
    assert types == ["text/plain", "text/html"]


def test_reply_to_and_empty_headers_are_handled() -> None:
    mime = _message(reply_to="ops@bidradar.example", headers={"X-Empty": ""}).as_mime()
    assert mime["Reply-To"] == "ops@bidradar.example"
    assert mime["X-Empty"] is None
    plain = _message(from_name="", to_name=None)
    assert plain.from_header == "alerts@bidradar.example"
    assert plain.to_header == "buyer@example.com"


# --- memory ----------------------------------------------------------------------------------


async def test_memory_provider_records_the_message() -> None:
    provider = MemoryProvider()
    result = await provider.send(_message())
    assert result.ok and result.provider_ref == "memory-1"
    assert provider.messages[0].subject.startswith("High fit")


# --- SMTP ------------------------------------------------------------------------------------


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: float = 0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.sent: list[Any] = []
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, message: Any) -> None:
        self.sent.append(message)


async def test_smtp_provider_sends_through_smtplib(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    provider = SMTPProvider("mail.local", 1025, username="u", password="p", starttls=True)
    result = await provider.send(_message())
    assert result.ok
    client = FakeSMTP.instances[-1]
    assert (client.host, client.port) == ("mail.local", 1025)
    assert client.started_tls and client.login_args == ("u", "p")
    assert client.sent[0]["Subject"].startswith("High fit")


async def test_smtp_provider_reports_connection_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    result = await SMTPProvider("nowhere", 1).send(_message())
    assert not result.ok and result.error is not None and "connection refused" in result.error


async def test_smtp_provider_reports_protocol_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    class Rejecting(FakeSMTP):
        def send_message(self, message: Any) -> None:
            raise smtplib.SMTPRecipientsRefused({})

    monkeypatch.setattr(smtplib, "SMTP", Rejecting)
    result = await SMTPProvider("mail.local", 1025).send(_message())
    assert not result.ok and result.error is not None and "SMTPRecipientsRefused" in result.error


# --- SendGrid --------------------------------------------------------------------------------


@respx.mock
async def test_sendgrid_posts_the_v3_body() -> None:
    route = respx.post("https://api.sendgrid.com/v3/mail/send").mock(
        return_value=httpx.Response(202, headers={"X-Message-Id": "sg-1"})
    )
    async with httpx.AsyncClient() as client:
        result = await SendGridProvider("SG.key", client=client).send(
            _message(reply_to="ops@bidradar.example")
        )
    assert result.ok and result.provider_ref == "sg-1"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer SG.key"
    body = __import__("json").loads(request.content)
    assert body["personalizations"][0]["to"] == [
        {"email": "buyer@example.com", "name": "Ada Lovelace"}
    ]
    assert body["from"] == {"email": "alerts@bidradar.example", "name": "BidRadar"}
    assert [c["type"] for c in body["content"]] == ["text/plain", "text/html"]
    assert body["headers"]["List-Unsubscribe"] == "<https://api/u/1>"
    assert body["reply_to"] == {"email": "ops@bidradar.example"}
    assert body["categories"] == ["high_fit_match"]


@respx.mock
async def test_sendgrid_reports_http_and_transport_errors() -> None:
    respx.post("https://api.sendgrid.com/v3/mail/send").mock(
        return_value=httpx.Response(413, text="payload too large")
    )
    async with httpx.AsyncClient() as client:
        rejected = await SendGridProvider("SG.key", client=client).send(_message())
    assert not rejected.ok and rejected.error is not None and "HTTP 413" in rejected.error

    respx.post("https://api.sendgrid.com/v3/mail/send").mock(side_effect=httpx.ConnectError("x"))
    async with httpx.AsyncClient() as client:
        down = await SendGridProvider("SG.key", client=client).send(_message())
    assert not down.ok and down.error is not None and "ConnectError" in down.error


@respx.mock
async def test_sendgrid_owns_its_client_when_none_is_injected() -> None:
    respx.post("https://api.sendgrid.com/v3/mail/send").mock(return_value=httpx.Response(202))
    assert (await SendGridProvider("SG.key").send(_message())).ok


# --- SES -------------------------------------------------------------------------------------


class FakeSESClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeSESClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send_raw_email(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": "ses-1"}


class FakeSESSession:
    def __init__(self, client: FakeSESClient) -> None:
        self.client = client
        self.client_kwargs: dict[str, Any] = {}

    def create_client(self, service: str, **kwargs: Any) -> FakeSESClient:
        self.service = service
        self.client_kwargs = kwargs
        return self.client


def _ses(monkeypatch: pytest.MonkeyPatch, client: FakeSESClient, **kwargs: Any) -> SESProvider:
    import aiobotocore.session

    session = FakeSESSession(client)
    monkeypatch.setattr(aiobotocore.session, "get_session", lambda: session)
    provider = SESProvider("ap-south-1", **kwargs)
    provider._session = session  # type: ignore[assignment]
    return provider


async def test_ses_sends_raw_email(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeSESClient()
    provider = _ses(monkeypatch, client, access_key="AK", secret_key="SK")
    result = await provider.send(_message())
    assert result.ok and result.provider_ref == "ses-1"
    call = client.calls[0]
    assert call["Source"] == "BidRadar <alerts@bidradar.example>"
    assert call["Destinations"] == ["buyer@example.com"]
    assert b"List-Unsubscribe" in call["RawMessage"]["Data"]
    assert provider._session.client_kwargs["region_name"] == "ap-south-1"  # type: ignore[attr-defined]
    assert provider._session.client_kwargs["aws_access_key_id"] == "AK"  # type: ignore[attr-defined]


async def test_ses_failure_is_a_failed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _ses(monkeypatch, FakeSESClient(error=RuntimeError("Throttling")))
    result = await provider.send(_message())
    assert not result.ok and result.error is not None and "Throttling" in result.error


def test_ses_region_follows_data_residency() -> None:
    settings = _settings()
    assert ses_region_for(settings, Region.IN) == "ap-south-1"
    assert ses_region_for(settings, "in") == "ap-south-1"
    assert ses_region_for(settings, Region.US) == "us-east-1"
    assert ses_region_for(settings, None) == "us-east-1"
    assert ses_region_for(_settings(region="in"), None) == "ap-south-1"


# --- provider selection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (EmailProviderName.SMTP, SMTPProvider),
        (EmailProviderName.SENDGRID, SendGridProvider),
        (EmailProviderName.MEMORY, MemoryProvider),
    ],
)
def test_build_email_provider(name: EmailProviderName, expected: type) -> None:
    assert isinstance(build_email_provider(_settings(email_provider=name)), expected)


def test_build_email_provider_ses_uses_the_tenant_region(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = build_email_provider(_settings(email_provider="ses"), region=Region.IN)
    assert isinstance(provider, SESProvider) and provider.region_name == "ap-south-1"


# --- channel ----------------------------------------------------------------------------------


async def test_channel_sends_and_carries_the_one_click_headers() -> None:
    settings = _settings()
    provider = MemoryProvider()
    channel = EmailChannel(settings, provider=provider, now=lambda: NOW)
    notification = _notification()
    result = await channel.send(
        None, notification, Recipient(user_id=USER, channels=("email",), email="a@example.com")
    )
    assert result.ok
    message = provider.messages[0]
    assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert message.headers["List-Unsubscribe"].startswith("<http")
    assert message.headers["X-BidRadar-Notification"] == str(notification.id)
    assert message.category == "high_fit_match"


async def test_channel_skips_an_unsubscribed_category_without_retrying() -> None:
    provider = MemoryProvider()
    channel = EmailChannel(_settings(), provider=provider)
    result = await channel.send(
        None,
        _notification(),
        Recipient(
            user_id=USER,
            channels=("email",),
            email="a@example.com",
            unsubscribed=("high_fit_match",),
        ),
    )
    assert result.skipped and not result.ok and provider.messages == []


async def test_channel_skips_unsubscribe_all_and_a_missing_address() -> None:
    provider = MemoryProvider()
    channel = EmailChannel(_settings(), provider=provider)
    opted_out = Recipient(
        user_id=USER, channels=("email",), email="a@example.com", unsubscribed=("all",)
    )
    assert (await channel.send(None, _notification(), opted_out)).skipped
    no_address = Recipient(user_id=USER, channels=("email",), email=None)
    assert (await channel.send(None, _notification(), no_address)).skipped
    assert provider.messages == []


async def test_channel_passes_a_provider_failure_through() -> None:
    class Broken:
        name = "broken"

        async def send(self, message: OutboundEmail) -> Any:
            from app.notify.core import SendResult

            return SendResult.failed("provider down")

    channel = EmailChannel(_settings(), provider=Broken())
    result = await channel.send(
        None, _notification(), Recipient(user_id=USER, channels=("email",), email="a@example.com")
    )
    assert not result.ok and not result.skipped and result.error == "provider down"


def test_build_message_renders_in_the_recipients_timezone() -> None:
    notification = _notification(
        payload={
            "title": "Cloud migration services",
            "response_due_at": "2026-10-14T18:00:00+00:00",
            "buyer_tz": "America/New_York",
            "score": 82.5,
            "band": "high",
        }
    )
    message = build_message(
        notification, settings=_settings(), to="a@example.com", user_tz="Asia/Kolkata", now=NOW
    )
    assert "11:30 PM IST" in message.text
    assert "3d 6h" in message.text
