"""Microsoft Teams channel (SPEC 7): an Adaptive Card posted to an incoming webhook.

    channel = TeamsChannel(settings, resolver=teams_resolver(database))

Teams has no interactive callback of our own, so the card's actions are Action.OpenUrl
links pointing at the same signed one-click URLs the email carries
(GET /api/v1/notifications/actions/{token}): one click still records Pursue / Watch /
Pass / Assign against the notification.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.display_time import countdown, render_tz
from app.models import IntegrationKind, Notification
from app.notify.core import SendResult
from app.notify.render import ACTION_LABELS, money_display
from app.notify.slack import load_integration, slack_settings_from

log = structlog.get_logger(__name__)

TEAMS = "teams"
CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
CARD_VERSION = "1.4"
MAX_BULLETS = 3


@dataclass(frozen=True, slots=True)
class TeamsSettings:
    webhook_url: str | None
    config: dict[str, Any]
    enabled: bool = True


TeamsResolver = Callable[[uuid.UUID], Awaitable[TeamsSettings | None]]


async def load_teams_settings(session: AsyncSession, tenant_id: uuid.UUID) -> TeamsSettings | None:
    """The tenant's Teams webhook; shares the integrations row and secret_ref scheme."""
    resolved = slack_settings_from(
        await load_integration(session, tenant_id, IntegrationKind.TEAMS.value)
    )
    if resolved is None:
        return None
    return TeamsSettings(resolved.webhook_url, resolved.config, resolved.enabled)


def _text_block(text: str, **extra: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **extra}


def _facts(payload: dict[str, Any], now: datetime) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    buyer = payload.get("buyer") or payload.get("buyer_org")
    if buyer:
        facts.append({"title": "Buyer", "value": str(buyer)})
    value = money_display(payload)
    if value:
        facts.append({"title": "Value", "value": value})
    raw_due = payload.get("response_due_at")
    if isinstance(raw_due, str) and raw_due:
        try:
            due = datetime.fromisoformat(raw_due)
        except ValueError:
            due = None
        if due is not None:
            due = due if due.tzinfo else due.replace(tzinfo=UTC)
            buyer_tz = str(payload.get("buyer_tz") or "UTC")
            facts.append(
                {
                    "title": "Response due",
                    "value": f"{render_tz(due, buyer_tz)} ({countdown(now, due)})",
                }
            )
    if payload.get("score") is not None:
        band = payload.get("band")
        facts.append({"title": "Fit", "value": f"{payload['score']}{f' ({band})' if band else ''}"})
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


def build_card(notification: Notification, *, now: datetime | None = None) -> dict[str, Any]:
    """The `attachments` envelope Teams expects around an Adaptive Card."""
    moment = now or datetime.now(UTC)
    payload = dict(notification.payload or {})
    title = str(payload.get("title") or notification.event_type.replace("_", " ").title())
    body: list[dict[str, Any]] = [
        _text_block(
            notification.event_type.replace("_", " ").title(),
            weight="Bolder",
            spacing="None",
            isSubtle=True,
        ),
        _text_block(title, size="Large", weight="Bolder"),
    ]
    facts = _facts(payload, moment)
    if facts:
        body.append({"type": "FactSet", "facts": facts})
    bullets = _bullets(payload)
    if bullets:
        body.append(_text_block("\n".join(f"- {b}" for b in bullets)))
    if payload.get("top_gap"):
        body.append(_text_block(f"**Top gap:** {payload['top_gap']}"))
    actions: list[dict[str, str]] = []
    links = payload.get("actions")
    if isinstance(links, dict):
        actions.extend(
            {"type": "Action.OpenUrl", "title": label, "url": str(links[name])}
            for name, label in ACTION_LABELS
            if links.get(name)
        )
    if payload.get("deep_link"):
        actions.append(
            {"type": "Action.OpenUrl", "title": "Open", "url": str(payload["deep_link"])}
        )
    card: dict[str, Any] = {
        "$schema": CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": CARD_VERSION,
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return {
        "type": "message",
        "attachments": [{"contentType": CARD_CONTENT_TYPE, "content": card}],
    }


class TeamsChannel:
    """notify.core.Channel posting an Adaptive Card to a tenant's Teams webhook."""

    name = TEAMS

    def __init__(
        self,
        settings: Settings,
        *,
        webhook_url: str | None = None,
        resolver: TeamsResolver | None = None,
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

    async def _target(self, tenant_id: uuid.UUID) -> TeamsSettings | None:
        if self.webhook_url:
            return TeamsSettings(self.webhook_url, {}, True)
        return await self.resolver(tenant_id) if self.resolver is not None else None

    async def send(self, delivery: Any, notification: Notification, recipient: Any) -> SendResult:
        target = await self._target(notification.tenant_id)
        if target is None or not target.enabled or not target.webhook_url:
            return SendResult.skip("teams is not configured for this tenant")
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(
                target.webhook_url, json=build_card(notification, now=self._now())
            )
        except httpx.HTTPError as exc:
            return SendResult.failed(f"teams: {type(exc).__name__}: {exc}")
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            return SendResult.failed(f"teams: HTTP {response.status_code} {response.text[:200]}")
        return SendResult.sent(provider_ref=response.headers.get("request-id"))
