"""Email channel (SPEC 7): providers, the MIME build and the Dispatcher Channel.

    provider = build_email_provider(settings)            # EMAIL_PROVIDER: smtp locally
    channel = EmailChannel(settings, provider=provider)  # plugs into notify.core.Dispatcher
    result = await provider.send(message)                # SendResult

Providers all satisfy `EmailProvider` and never raise for a provider error (they answer
SendResult.failed so the Dispatcher can retry and then fall back):

  SESProvider       aiobotocore `ses:SendRawEmail`; the region follows data residency —
                    ap-south-1 for Indian tenants so the message never leaves India,
                    SES_REGION_US otherwise (SPEC 7, 11).
  SendGridProvider  httpx POST /v3/mail/send with the custom headers attached.
  SMTPProvider      stdlib smtplib in a worker thread (Mailpit on localhost:1025 locally;
                    STARTTLS + auth in staging).
  MemoryProvider    keeps the messages in a list; the default in tests and for a dev box
                    with no mail server.

Every message carries `List-Unsubscribe` / `List-Unsubscribe-Post` (RFC 2369 / RFC 8058)
pointing at the per-category unsubscribe link, and `EmailChannel` skips a delivery whose
category the recipient has unsubscribed from (CAN-SPAM).
"""

from __future__ import annotations

import asyncio
import smtplib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formataddr, make_msgid
from typing import Any, Protocol

import httpx
import structlog

from app.core.config import EmailProviderName, Region, Settings
from app.models import Notification
from app.notify.core import SendResult
from app.notify.render import RenderedEmail, email_context, render_email
from app.notify.unsubscribe import ALL, is_unsubscribed, unsubscribe_url

log = structlog.get_logger(__name__)

EMAIL = "email"
SENDGRID_PATH = "/v3/mail/send"
DEFAULT_TIMEOUT = 15.0
# CRLF line endings, and folding only at the RFC 5322 hard limit: the default policy folds
# at 78 and then RFC 2047-encodes what will not fit, which mangles the List-Unsubscribe URL
# into "=?utf-8?q?=3Chttp..." and breaks one-click unsubscribe in every mail client.
MIME_POLICY = SMTP_POLICY.clone(max_line_length=998)


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    to: str
    subject: str
    html: str
    text: str
    from_email: str
    from_name: str = ""
    to_name: str | None = None
    reply_to: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    # notification category, for provider-side suppression groups and logging
    category: str | None = None

    @property
    def from_header(self) -> str:
        return formataddr((self.from_name, self.from_email)) if self.from_name else self.from_email

    @property
    def to_header(self) -> str:
        return formataddr((self.to_name, self.to)) if self.to_name else self.to

    def as_mime(self, *, message_id: str | None = None) -> EmailMessage:
        """multipart/alternative with the text part first (what every client expects)."""
        mime = EmailMessage(policy=MIME_POLICY)
        mime["Subject"] = self.subject
        mime["From"] = self.from_header
        mime["To"] = self.to_header
        mime["Message-ID"] = message_id or make_msgid(domain=self.from_email.rsplit("@", 1)[-1])
        mime["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S %z")
        if self.reply_to:
            mime["Reply-To"] = self.reply_to
        for name, value in self.headers.items():
            if value:
                mime[name] = value
        mime.set_content(self.text)
        mime.add_alternative(self.html, subtype="html")
        return mime


class EmailProvider(Protocol):
    name: str

    async def send(self, message: OutboundEmail) -> SendResult: ...


# --- providers -------------------------------------------------------------------------------


class MemoryProvider:
    """Collects messages instead of sending; default when no provider is configured."""

    name = EmailProviderName.MEMORY.value

    def __init__(self) -> None:
        self.messages: list[OutboundEmail] = []

    async def send(self, message: OutboundEmail) -> SendResult:
        self.messages.append(message)
        log.info("notify.email.memory", to=message.to, subject=message.subject)
        return SendResult.sent(provider_ref=f"memory-{len(self.messages)}")


class SMTPProvider:
    """stdlib smtplib in a worker thread (no async SMTP dependency); Mailpit locally."""

    name = EmailProviderName.SMTP.value

    def __init__(
        self,
        host: str,
        port: int,
        *,
        username: str = "",
        password: str = "",
        starttls: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.starttls = starttls
        self.timeout = timeout

    def _send_sync(self, mime: EmailMessage) -> str:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
            if self.starttls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(mime)
        return str(mime["Message-ID"] or "")

    async def send(self, message: OutboundEmail) -> SendResult:
        mime = message.as_mime()
        try:
            ref = await asyncio.to_thread(self._send_sync, mime)
        except (OSError, smtplib.SMTPException) as exc:
            return SendResult.failed(f"smtp: {type(exc).__name__}: {exc}")
        return SendResult.sent(provider_ref=ref or None)


class SESProvider:
    """AWS SES through aiobotocore (already a dependency for object storage)."""

    name = EmailProviderName.SES.value

    def __init__(
        self,
        region_name: str,
        *,
        access_key: str = "",
        secret_key: str = "",
        endpoint_url: str = "",
    ) -> None:
        from aiobotocore.session import get_session

        self.region_name = region_name
        self._session = get_session()
        self._client_kwargs: dict[str, Any] = {"region_name": region_name}
        if endpoint_url:
            self._client_kwargs["endpoint_url"] = endpoint_url
        if access_key:
            self._client_kwargs["aws_access_key_id"] = access_key
            self._client_kwargs["aws_secret_access_key"] = secret_key

    async def send(self, message: OutboundEmail) -> SendResult:
        raw = message.as_mime().as_bytes()
        try:
            async with self._session.create_client("ses", **self._client_kwargs) as ses:
                response = await ses.send_raw_email(
                    Source=message.from_header,
                    Destinations=[message.to],
                    RawMessage={"Data": raw},
                )
        except Exception as exc:  # botocore raises a family of ClientError subclasses
            return SendResult.failed(f"ses: {type(exc).__name__}: {exc}")
        return SendResult.sent(provider_ref=str(response.get("MessageId") or "") or None)


class SendGridProvider:
    name = EmailProviderName.SENDGRID.value

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.sendgrid.com",
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.timeout = timeout

    def _body(self, message: OutboundEmail) -> dict[str, Any]:
        personalization: dict[str, Any] = {
            "to": [{"email": message.to, **({"name": message.to_name} if message.to_name else {})}]
        }
        body: dict[str, Any] = {
            "personalizations": [personalization],
            "from": {
                "email": message.from_email,
                **({"name": message.from_name} if message.from_name else {}),
            },
            "subject": message.subject,
            "content": [
                {"type": "text/plain", "value": message.text},
                {"type": "text/html", "value": message.html},
            ],
        }
        headers = {k: v for k, v in message.headers.items() if v}
        if headers:
            body["headers"] = headers
        if message.reply_to:
            body["reply_to"] = {"email": message.reply_to}
        if message.category:
            body["categories"] = [message.category]
        return body

    async def send(self, message: OutboundEmail) -> SendResult:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(
                f"{self.base_url}{SENDGRID_PATH}",
                json=self._body(message),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            return SendResult.failed(f"sendgrid: {type(exc).__name__}: {exc}")
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            return SendResult.failed(f"sendgrid: HTTP {response.status_code} {response.text[:200]}")
        return SendResult.sent(provider_ref=response.headers.get("X-Message-Id"))


def ses_region_for(settings: Settings, region: Region | str | None) -> str:
    """Indian tenants are served from SES Mumbai so mail stays in-region (SPEC 7, 11)."""
    chosen = Region(region) if region is not None else settings.region
    return settings.ses_region_in if chosen is Region.IN else settings.ses_region_us


def build_email_provider(
    settings: Settings, *, region: Region | str | None = None
) -> EmailProvider:
    """The provider named by EMAIL_PROVIDER; SES picks its region from data residency."""
    match settings.email_provider:
        case EmailProviderName.SES:
            return SESProvider(ses_region_for(settings, region))
        case EmailProviderName.SENDGRID:
            return SendGridProvider(settings.sendgrid_api_key, base_url=settings.sendgrid_base_url)
        case EmailProviderName.SMTP:
            return SMTPProvider(
                settings.smtp_host,
                settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                starttls=settings.smtp_starttls,
                timeout=settings.smtp_timeout_seconds,
            )
        case _:
            return MemoryProvider()


# --- channel ---------------------------------------------------------------------------------


def build_message(
    notification: Notification,
    *,
    settings: Settings,
    to: str,
    to_name: str | None = None,
    user_tz: str = "UTC",
    locale: str = "en",
    now: datetime | None = None,
) -> OutboundEmail:
    """Render one notification into the outbound message, headers included."""
    context = email_context(
        notification.payload,
        event_type=notification.event_type,
        settings=settings,
        tenant_id=notification.tenant_id,
        user_id=notification.user_id,
        user_tz=user_tz,
        locale=locale,
        now=now,
    )
    rendered: RenderedEmail = render_email(notification.event_type, context)
    one_click = unsubscribe_url(
        settings,
        tenant_id=notification.tenant_id,
        user_id=notification.user_id,
        category=notification.event_type,
        now=now,
    )
    headers = {
        "List-Unsubscribe": f"<{one_click}>, <mailto:{settings.contact_email}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "List-Id": f"{notification.event_type} <notify.bidradar>",
        "X-BidRadar-Event": notification.event_type,
        "X-BidRadar-Notification": str(notification.id),
    }
    return OutboundEmail(
        to=to,
        to_name=to_name,
        subject=rendered.subject,
        html=rendered.html,
        text=rendered.text,
        from_email=settings.email_from,
        from_name=settings.email_from_name,
        reply_to=settings.email_reply_to or None,
        headers=headers,
        category=notification.event_type,
    )


class EmailChannel:
    """notify.core.Channel over an EmailProvider."""

    name = EMAIL

    def __init__(
        self,
        settings: Settings,
        *,
        provider: EmailProvider | None = None,
        now: Any = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or build_email_provider(settings)
        self._now = now

    def _clock(self) -> datetime:
        return self._now() if callable(self._now) else datetime.now(UTC)

    async def send(self, delivery: Any, notification: Notification, recipient: Any) -> SendResult:
        if not recipient.email:
            return SendResult.skip("recipient has no email address")
        if is_unsubscribed(notification.event_type, getattr(recipient, "unsubscribed", ())):
            return SendResult.skip(f"unsubscribed from {notification.event_type}")
        message = build_message(
            notification,
            settings=self.settings,
            to=recipient.email,
            to_name=recipient.name,
            user_tz=recipient.tz,
            locale=getattr(recipient, "locale", "en"),
            now=self._clock(),
        )
        result = await self.provider.send(message)
        log.info(
            "notify.email.sent" if result.ok else "notify.email.failed",
            provider=self.provider.name,
            to=recipient.email,
            event_type=notification.event_type,
            error=result.error,
        )
        return result


def unsubscribe_all_url(settings: Settings, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return unsubscribe_url(settings, tenant_id=tenant_id, user_id=user_id, category=ALL)
