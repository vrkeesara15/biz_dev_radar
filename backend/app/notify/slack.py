"""Slack channel (SPEC 7, 10.3): Block Kit messages and the interactive action callback.

    settings_for = await load_integration(session, tenant_id, IntegrationKind.SLACK)
    channel = SlackChannel(app_settings, resolver=slack_resolver(database))
    blocks = build_blocks(notification, settings=app_settings)

Outbound: an incoming webhook POST carrying a Block Kit message with Pursue / Pass /
Assign buttons and the opportunity link. Each button's `value` is the signed action token
minted by app.notify.actions for that notification, so a click funnels into exactly the
same `record_action` as the email links; nothing in the Slack payload is trusted to name
the tenant or the user.

Inbound: POST /api/v1/integrations/slack/actions. The request must carry
`X-Slack-Signature: v0=<hmac sha256 of "v0:<timestamp>:<raw body>" under the tenant's
signing secret>` and a timestamp no more than SLACK_TIMESTAMP_TOLERANCE_SECONDS old
(replay window). The tenant is established by the action token inside the payload, then
that tenant's signing secret verifies the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.display_time import countdown, render_tz
from app.models import Integration, IntegrationKind, Notification
from app.notify.core import SendResult
from app.notify.render import ACTION_LABELS, money_display
from app.services.secrets import SecretUnavailableError, resolve_secret_mapping

log = structlog.get_logger(__name__)

SLACK = "slack"
ACTION_PREFIX = "bidradar_"
SIGNATURE_HEADER = "x-slack-signature"
TIMESTAMP_HEADER = "x-slack-request-timestamp"
SIGNATURE_VERSION = "v0"
TIMESTAMP_TOLERANCE_SECONDS = 300  # Slack's own replay window
WEBHOOK_KEY = "webhook_url"
SIGNING_SECRET_KEY = "signing_secret"
# SPEC 7 names Pursue / Pass / Assign on the Slack card; Watch stays an email/in-app action
# because a Slack button cannot collect the Pass reason without the app-level modal (OQ-77).
BUTTON_ACTIONS: tuple[str, ...] = ("pursue", "pass", "assign")
MAX_BULLETS = 3


class SlackSignatureError(ValueError):
    """The request is not a genuine, fresh Slack callback."""


@dataclass(frozen=True, slots=True)
class SlackSettings:
    webhook_url: str | None
    signing_secret: str | None
    config: dict[str, Any]
    enabled: bool = True


WebhookResolver = Callable[[uuid.UUID], Awaitable[SlackSettings | None]]


# --- integration row --------------------------------------------------------------------------


async def load_integration(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str = IntegrationKind.SLACK.value
) -> Integration | None:
    return (
        await session.execute(
            select(Integration).where(Integration.tenant_id == tenant_id, Integration.kind == kind)
        )
    ).scalar_one_or_none()


def slack_settings_from(row: Integration | None) -> SlackSettings | None:
    """Resolve the row's secret reference; an unreadable secret is treated as unconfigured."""
    if row is None:
        return None
    try:
        secrets = resolve_secret_mapping(row.secret_ref)
    except SecretUnavailableError as exc:
        log.warning("notify.slack.secret_unavailable", tenant_id=str(row.tenant_id), error=str(exc))
        secrets = {}
    return SlackSettings(
        webhook_url=secrets.get(WEBHOOK_KEY),
        signing_secret=secrets.get(SIGNING_SECRET_KEY),
        config=dict(row.config or {}),
        enabled=bool(row.enabled),
    )


async def load_slack_settings(session: AsyncSession, tenant_id: uuid.UUID) -> SlackSettings | None:
    return slack_settings_from(await load_integration(session, tenant_id))


# --- Block Kit --------------------------------------------------------------------------------


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _facts(payload: dict[str, Any], *, now: datetime) -> list[str]:
    facts: list[str] = []
    if payload.get("buyer") or payload.get("buyer_org"):
        facts.append(f"*Buyer*\n{payload.get('buyer') or payload.get('buyer_org')}")
    value = money_display(payload)
    if value:
        facts.append(f"*Value*\n{value}")
    raw_due = payload.get("response_due_at")
    if isinstance(raw_due, str) and raw_due:
        try:
            due = datetime.fromisoformat(raw_due)
        except ValueError:
            due = None
        if due is not None:
            due = due if due.tzinfo else due.replace(tzinfo=UTC)
            buyer_tz = str(payload.get("buyer_tz") or "UTC")
            facts.append(f"*Due*\n{render_tz(due, buyer_tz)} ({countdown(now, due)})")
    if payload.get("score") is not None:
        band = payload.get("band")
        facts.append(f"*Fit*\n{payload['score']}{f' ({band})' if band else ''}")
    return facts


def _bullets(payload: dict[str, Any]) -> list[str]:
    rationale = payload.get("rationale")
    if isinstance(rationale, dict):
        rationale = rationale.get("fit_summary")
    if isinstance(rationale, str):
        rationale = [rationale]
    if not isinstance(rationale, list):
        return []
    return [str(b).strip() for b in rationale if str(b).strip()][:MAX_BULLETS]


def build_blocks(notification: Notification, *, now: datetime | None = None) -> dict[str, Any]:
    """The Block Kit body posted to the incoming webhook."""
    moment = now or datetime.now(UTC)
    payload = dict(notification.payload or {})
    title = str(payload.get("title") or notification.event_type.replace("_", " ").title())
    link = payload.get("deep_link")
    headline = f"<{link}|{title}>" if link else title
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _header_text(notification, payload)[:150]},
        },
        _section(f"*{headline}*"),
    ]
    facts = _facts(payload, now=moment)
    if facts:
        blocks.append(
            {"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in facts[:10]]}
        )
    bullets = _bullets(payload)
    if bullets:
        blocks.append(_section("\n".join(f"• {b}" for b in bullets)))
    elements = _buttons(payload, link)
    if elements:
        blocks.append({"type": "actions", "block_id": str(notification.id), "elements": elements})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"BidRadar · {notification.event_type.replace('_', ' ')}",
                }
            ],
        }
    )
    return {"text": _header_text(notification, payload), "blocks": blocks}


def _header_text(notification: Notification, payload: dict[str, Any]) -> str:
    title = str(payload.get("title") or notification.event_type.replace("_", " ").title())
    if notification.event_type == "high_fit_match" and payload.get("score") is not None:
        return f"{str(payload.get('band') or 'high').title()} fit {payload['score']}: {title}"
    return f"{notification.event_type.replace('_', ' ').title()}: {title}"


def _buttons(payload: dict[str, Any], link: Any) -> list[dict[str, Any]]:
    actions = payload.get("actions")
    if not isinstance(actions, dict):
        return []
    labels = dict(ACTION_LABELS)
    elements: list[dict[str, Any]] = []
    for name in BUTTON_ACTIONS:
        url = actions.get(name)
        if not url:
            continue
        token = str(url).rstrip("/").rsplit("/", 1)[-1]
        element: dict[str, Any] = {
            "type": "button",
            "action_id": f"{ACTION_PREFIX}{name}",
            "text": {"type": "plain_text", "text": labels.get(name, name.title())},
            "value": token,
        }
        if name == "pursue":
            element["style"] = "primary"
        if name == "pass":
            element["style"] = "danger"
        elements.append(element)
    if link and elements:
        elements.append(
            {
                "type": "button",
                "action_id": f"{ACTION_PREFIX}open",
                "text": {"type": "plain_text", "text": "Open"},
                "url": str(link),
            }
        )
    return elements


# --- outbound channel -------------------------------------------------------------------------


class SlackChannel:
    """notify.core.Channel posting to a tenant's Slack incoming webhook."""

    name = SLACK

    def __init__(
        self,
        settings: Settings,
        *,
        webhook_url: str | None = None,
        resolver: WebhookResolver | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.webhook_url = webhook_url
        self.resolver = resolver
        self._client = client
        self.timeout = timeout
        self._now = now or (lambda: datetime.now(UTC))

    async def _target(self, tenant_id: uuid.UUID) -> SlackSettings | None:
        if self.webhook_url:
            return SlackSettings(self.webhook_url, None, {}, True)
        return await self.resolver(tenant_id) if self.resolver is not None else None

    async def send(self, delivery: Any, notification: Notification, recipient: Any) -> SendResult:
        target = await self._target(notification.tenant_id)
        if target is None or not target.enabled or not target.webhook_url:
            return SendResult.skip("slack is not configured for this tenant")
        body = build_blocks(notification, now=self._now())
        if target.config.get("channel"):
            body["channel"] = target.config["channel"]
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(target.webhook_url, json=body)
        except httpx.HTTPError as exc:
            return SendResult.failed(f"slack: {type(exc).__name__}: {exc}")
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            return SendResult.failed(f"slack: HTTP {response.status_code} {response.text[:200]}")
        return SendResult.sent(provider_ref=response.headers.get("X-Slack-Req-Id"))


# --- inbound callback ---------------------------------------------------------------------------


def sign_request(signing_secret: str, timestamp: str, body: bytes) -> str:
    """Slack's v0 signature: HMAC-SHA256 of "v0:<timestamp>:<raw body>"."""
    basestring = b":".join((SIGNATURE_VERSION.encode(), timestamp.encode(), body))
    digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_signature(
    signing_secret: str | None,
    *,
    signature: str | None,
    timestamp: str | None,
    body: bytes,
    now: datetime | None = None,
    tolerance_seconds: int = TIMESTAMP_TOLERANCE_SECONDS,
) -> None:
    """Raise SlackSignatureError unless the signature is valid and the timestamp is fresh."""
    if not signing_secret:
        raise SlackSignatureError("slack signing secret is not configured")
    if not signature or not timestamp:
        raise SlackSignatureError("missing slack signature headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise SlackSignatureError("malformed slack timestamp") from exc
    moment = int((now or datetime.now(UTC)).timestamp())
    if abs(moment - sent_at) > tolerance_seconds:
        raise SlackSignatureError("slack timestamp outside the replay window")
    if not hmac.compare_digest(sign_request(signing_secret, timestamp, body), signature):
        raise SlackSignatureError("slack signature mismatch")


@dataclass(frozen=True, slots=True)
class SlackAction:
    action: str
    token: str
    user: str | None
    team_id: str | None
    response_url: str | None


def parse_block_actions(body: bytes) -> SlackAction:
    """Pull the first BidRadar button out of an `interactivity` form post.

    Slack sends `application/x-www-form-urlencoded` with a single `payload` field holding
    the JSON. Nothing here is trusted: the button's value is our own signed action token,
    which is what actually names the tenant, user and notification.
    """
    from urllib.parse import parse_qs

    form = parse_qs(body.decode("utf-8", errors="replace"))
    raw = (form.get("payload") or [""])[0]
    if not raw:
        raise SlackSignatureError("slack payload is missing")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SlackSignatureError("slack payload is not JSON") from exc
    if payload.get("type") != "block_actions":
        raise SlackSignatureError(f"unsupported slack payload type {payload.get('type')!r}")
    for action in payload.get("actions") or []:
        action_id = str(action.get("action_id") or "")
        if not action_id.startswith(ACTION_PREFIX):
            continue
        name = action_id[len(ACTION_PREFIX) :]
        if name not in BUTTON_ACTIONS:
            continue
        return SlackAction(
            action=name,
            token=str(action.get("value") or ""),
            user=(payload.get("user") or {}).get("username")
            or (payload.get("user") or {}).get("id"),
            team_id=(payload.get("team") or {}).get("id"),
            response_url=payload.get("response_url"),
        )
    raise SlackSignatureError("no BidRadar action in the slack payload")


def action_token_from(body: bytes) -> str:
    """The signed action token inside a callback, read before any signature check so the
    tenant (and therefore the signing secret) can be looked up."""
    return parse_block_actions(body).token
