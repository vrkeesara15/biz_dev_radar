"""WhatsApp Business channel through a BSP (SPEC 7, 12; M6-05).

    channel = WhatsAppChannel(settings, eligibility=whatsapp_eligibility(database))
    result = await channel.send(delivery, notification, recipient)

WhatsApp Business only allows business-initiated messages that use a PRE-APPROVED
template, so nothing here composes free text: `template_for(event_type)` picks a template
name from settings and `template_variables` fills its numbered placeholders. An event
with no configured template is skipped, never sent as a plain message.

SPEC 7 puts WhatsApp on exactly two rows — the deadline reminder ladder and the high-fit
alert — and only for Indian tenants. `WhatsAppChannel` enforces all three gates before it
touches the network:

  1. the tenant's region is `in`;
  2. the recipient has a verified number (`users.phone_e164` + `users.phone_verified_at`);
  3. the event has a template configured.

Providers (Gupshup and Twilio) sit behind `WhatsAppProvider` so tests mock HTTP. Delivery
receipts arrive at POST /api/v1/webhooks/whatsapp/{provider} and move the matching
`notification_deliveries` row to sent / failed / opened by its `provider_ref`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from app.core.config import Settings
from app.core.display_time import countdown, render_tz
from app.core.preferences import NotificationEvent
from app.models import Notification
from app.models.notifications import DeliveryStatus
from app.notify.core import Recipient, SendResult

log = structlog.get_logger(__name__)

WHATSAPP = "whatsapp"
GUPSHUP = "gupshup"
TWILIO = "twilio"
PROVIDERS: tuple[str, ...] = (GUPSHUP, TWILIO)

# SPEC 7: WhatsApp carries the deadline ladder and the high-fit alert, nothing else.
ALLOWED_EVENTS: tuple[str, ...] = (
    NotificationEvent.DEADLINE_REMINDER.value,
    NotificationEvent.HIGH_FIT_MATCH.value,
)
INDIA = "in"
MAX_VARIABLE_CHARS = 200
E164 = re.compile(r"^\+[1-9]\d{7,14}$")

SIGNATURE_HEADER_TWILIO = "x-twilio-signature"
SIGNATURE_HEADER_GUPSHUP = "x-gupshup-signature"

# BSP status vocabulary -> our delivery statuses
STATUS_MAP: dict[str, str] = {
    "sent": DeliveryStatus.SENT.value,
    "queued": DeliveryStatus.SENT.value,
    "submitted": DeliveryStatus.SENT.value,
    "accepted": DeliveryStatus.SENT.value,
    "delivered": DeliveryStatus.SENT.value,
    "read": DeliveryStatus.OPENED.value,
    "failed": DeliveryStatus.FAILED.value,
    "undelivered": DeliveryStatus.FAILED.value,
    "enqueued": DeliveryStatus.SENT.value,
}


def is_e164(value: str | None) -> bool:
    return bool(value) and bool(E164.match(str(value)))


@dataclass(frozen=True, slots=True)
class WhatsAppTarget:
    """Everything the channel needs to decide and to send, resolved outside the channel."""

    phone_e164: str | None = None
    phone_verified: bool = False
    region: str = "us"

    @property
    def eligible(self) -> bool:
        return self.region.lower() == INDIA and self.phone_verified and is_e164(self.phone_e164)


TargetResolver = Callable[[uuid.UUID, uuid.UUID], Awaitable[WhatsAppTarget]]


@dataclass(frozen=True, slots=True)
class TemplateMessage:
    """One pre-approved template plus its ordered variables."""

    name: str
    to: str
    variables: list[str]
    language: str = "en"


def template_for(settings: Settings, event_type: str) -> str | None:
    """The pre-approved template name configured for this event, if any."""
    if event_type == NotificationEvent.DEADLINE_REMINDER.value:
        return settings.whatsapp_template_deadline or None
    if event_type == NotificationEvent.HIGH_FIT_MATCH.value:
        return settings.whatsapp_template_high_match or None
    return None


def _clip(value: object) -> str:
    # WhatsApp rejects newlines and tabs inside template variables
    text = " ".join(str(value or "").split())
    return text[:MAX_VARIABLE_CHARS] or "-"


def template_variables(notification: Notification, recipient: Recipient) -> list[str]:
    """{{1}}..{{4}} for both templates: title, buyer, when, link.

    Deadline: "<title>", "<buyer>", "Oct 14, 2:00 PM EDT = 11:30 PM IST (in 3d 4h)", link.
    High fit: "<title>", "<buyer>", "<score> / 100", link.
    """
    payload = notification.payload or {}
    title = _clip(payload.get("title") or "a tracked opportunity")
    buyer = _clip(payload.get("buyer") or payload.get("buyer_org") or "-")
    link = _clip(payload.get("deep_link") or "-")
    if notification.event_type == NotificationEvent.HIGH_FIT_MATCH.value:
        score = payload.get("score")
        third = "-" if score is None else f"{score} / 100"
        return [title, buyer, _clip(third), link]
    return [title, buyer, _clip(_when(payload, recipient)), link]


def _when(payload: dict[str, Any], recipient: Recipient) -> str:
    from datetime import UTC, datetime

    raw = payload.get("due_at") or payload.get("response_due_at")
    if not raw:
        return "-"
    try:
        due = datetime.fromisoformat(str(raw))
    except ValueError:
        return "-"
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    buyer_tz = str(payload.get("buyer_tz") or "UTC")
    return f"{render_tz(due, buyer_tz, recipient.tz)} ({countdown(datetime.now(UTC), due)})"


class WhatsAppSendError(RuntimeError):
    """The BSP refused the message or could not be reached."""


class WhatsAppProvider(Protocol):
    name: str

    async def send_template(self, message: TemplateMessage) -> str:
        """The BSP's message id, which becomes notification_deliveries.provider_ref."""
        ...


class _BspProvider:
    name = "bsp"

    def __init__(
        self, settings: Settings, *, client: httpx.AsyncClient | None = None, timeout: float = 10.0
    ) -> None:
        self.settings = settings
        self._client = client
        self.timeout = timeout

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(url, **kwargs)
        except httpx.HTTPError as exc:
            raise WhatsAppSendError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            raise WhatsAppSendError(f"HTTP {response.status_code}: {response.text[:200]}")
        return response


class GupshupProvider(_BspProvider):
    """Gupshup's WhatsApp template API (form-encoded, apikey header)."""

    name = GUPSHUP

    def configured(self) -> bool:
        return bool(self.settings.gupshup_api_key and self.settings.gupshup_source_number)

    async def send_template(self, message: TemplateMessage) -> str:
        import json

        form = {
            "channel": "whatsapp",
            "source": self.settings.gupshup_source_number,
            "destination": message.to.lstrip("+"),
            "src.name": self.settings.gupshup_app_name,
            "template": json.dumps({"id": message.name, "params": message.variables}),
        }
        response = await self._post(
            f"{self.settings.gupshup_api_url.rstrip('/')}/template/msg",
            data=form,
            headers={"apikey": self.settings.gupshup_api_key, "accept": "application/json"},
        )
        body = response.json()
        message_id = body.get("messageId") or body.get("id")
        if not message_id:
            raise WhatsAppSendError("gupshup returned no message id")
        return str(message_id)


class TwilioProvider(_BspProvider):
    """Twilio's WhatsApp content API (form-encoded, basic auth)."""

    name = TWILIO

    def configured(self) -> bool:
        return bool(
            self.settings.twilio_account_sid
            and self.settings.twilio_auth_token
            and self.settings.twilio_whatsapp_from
        )

    async def send_template(self, message: TemplateMessage) -> str:
        import json

        variables = {str(index + 1): value for index, value in enumerate(message.variables)}
        form = {
            "From": f"whatsapp:{self.settings.twilio_whatsapp_from}",
            "To": f"whatsapp:{message.to}",
            "ContentSid": message.name,
            "ContentVariables": json.dumps(variables),
        }
        url = (
            f"{self.settings.twilio_api_url.rstrip('/')}/2010-04-01/Accounts/"
            f"{self.settings.twilio_account_sid}/Messages.json"
        )
        response = await self._post(
            url,
            data=form,
            auth=(self.settings.twilio_account_sid, self.settings.twilio_auth_token),
        )
        body = response.json()
        message_id = body.get("sid")
        if not message_id:
            raise WhatsAppSendError("twilio returned no message sid")
        return str(message_id)


def build_provider(
    settings: Settings, *, client: httpx.AsyncClient | None = None
) -> WhatsAppProvider | None:
    """The configured BSP, or None when WhatsApp is off for this deployment."""
    name = (settings.whatsapp_provider or "").strip().lower()
    if name == GUPSHUP:
        provider = GupshupProvider(settings, client=client)
        return provider if provider.configured() else None
    if name == TWILIO:
        twilio = TwilioProvider(settings, client=client)
        return twilio if twilio.configured() else None
    return None


class WhatsAppChannel:
    """notify.core.Channel for WhatsApp: three gates, then one template message."""

    name = WHATSAPP

    def __init__(
        self,
        settings: Settings,
        *,
        provider: WhatsAppProvider | None = None,
        eligibility: TargetResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.provider = (
            provider if provider is not None else build_provider(settings, client=client)
        )
        self.eligibility = eligibility

    async def _target(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> WhatsAppTarget:
        if self.eligibility is None:
            return WhatsAppTarget()
        return await self.eligibility(tenant_id, user_id)

    async def send(
        self, delivery: Any, notification: Notification, recipient: Recipient
    ) -> SendResult:
        if notification.event_type not in ALLOWED_EVENTS:
            return SendResult.skip(f"whatsapp does not carry {notification.event_type}")
        if self.provider is None:
            return SendResult.skip("whatsapp is not configured")
        template = template_for(self.settings, notification.event_type)
        if not template:
            return SendResult.skip(f"no approved whatsapp template for {notification.event_type}")
        target = await self._target(notification.tenant_id, recipient.user_id)
        if not target.eligible:
            return SendResult.skip(
                "whatsapp needs an Indian tenant and a verified number for this user"
            )
        assert target.phone_e164 is not None
        message = TemplateMessage(
            name=template,
            to=target.phone_e164,
            variables=template_variables(notification, recipient),
            language=self.settings.whatsapp_template_language,
        )
        try:
            provider_ref = await self.provider.send_template(message)
        except WhatsAppSendError as exc:
            return SendResult.failed(f"whatsapp: {exc}")
        return SendResult.sent(provider_ref=provider_ref)


# --- delivery receipts -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    provider_ref: str
    status: str  # a DeliveryStatus value
    error: str | None = None


def parse_receipt(provider: str, payload: dict[str, Any]) -> DeliveryReceipt | None:
    """One BSP status callback -> (message id, our delivery status). None when the payload
    is a message we do not track (an inbound reply, a heartbeat)."""
    if provider == TWILIO:
        message_id = payload.get("MessageSid") or payload.get("SmsSid")
        raw_status = str(payload.get("MessageStatus") or payload.get("SmsStatus") or "").lower()
        error = payload.get("ErrorMessage") or payload.get("ErrorCode")
    elif provider == GUPSHUP:
        nested = payload.get("payload")
        body: dict[str, Any] = nested if isinstance(nested, dict) else payload
        message_id = body.get("gsId") or body.get("id")
        raw_status = str(body.get("type") or payload.get("type") or "").lower()
        detail = body.get("payload")
        error = detail.get("reason") if isinstance(detail, dict) else None
    else:
        return None
    status = STATUS_MAP.get(raw_status)
    if not message_id or status is None:
        return None
    return DeliveryReceipt(
        provider_ref=str(message_id), status=status, error=None if error is None else str(error)
    )


class WhatsAppSignatureError(ValueError):
    """The receipt did not come from the configured BSP."""


def verify_receipt_signature(
    provider: str, settings: Settings, *, headers: dict[str, str], body: bytes
) -> None:
    """Verify the BSP signature where the BSP supports one.

    Gupshup signs the raw body with HMAC-SHA256 under the app's webhook secret. Twilio's
    scheme signs the URL plus the sorted POST parameters, which needs the public URL the
    request arrived on; until that is configured (`WHATSAPP_WEBHOOK_SECRET`), a Twilio
    receipt is accepted on the shared secret in the same header. A deployment with no
    secret configured accepts receipts unsigned — documented, and the receipt only ever
    moves a delivery row we already created.
    """
    secret = settings.whatsapp_webhook_secret
    if not secret:
        return
    header = SIGNATURE_HEADER_TWILIO if provider == TWILIO else SIGNATURE_HEADER_GUPSHUP
    supplied = headers.get(header) or headers.get(header.title()) or ""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied.strip(), expected):
        raise WhatsAppSignatureError("invalid whatsapp receipt signature")
