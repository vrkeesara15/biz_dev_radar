"""Channel wiring (SPEC 7): one place that knows how to build every channel.

    dispatcher = build_dispatcher(settings, database)
    await dispatcher.dispatch(session, event, recipients)

The event router (M4-14) and the reminder ladder (M6) take a Dispatcher built here rather
than constructing channels themselves. Each channel resolves its own per-tenant
configuration through a resolver that opens its own tenant-scoped session, so a channel
never needs the caller's session.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.core.config import Settings
from app.core.db import Database, get_database
from app.models import PushSubscription
from app.notify.core import Channel, Dispatcher
from app.notify.email import EmailChannel
from app.notify.in_app import InAppChannel
from app.notify.push import (
    PushChannel,
    Subscription,
    delete_push_subscription,
    load_push_subscriptions,
)
from app.notify.slack import SlackChannel, SlackSettings, load_slack_settings
from app.notify.teams import TeamsChannel, TeamsSettings, load_teams_settings

CHANNEL_NAMES: tuple[str, ...] = ("in_app", "email", "slack", "teams", "push")

# `core.preferences.NotificationChannel` (the vocabulary of user_notification_prefs and
# the settings UI) and the dispatcher's channel keys are not spelled the same; this is
# the single place that maps one onto the other (M4-08 / M4-14).
CHANNEL_ALIASES: dict[str, str] = {
    "web_push": "push",
    "webpush": "push",
    "in-app": "in_app",
    "inapp": "in_app",
    "bell": "in_app",
    # SPEC 7 lists WhatsApp for India, but no channel implementation exists yet; a
    # preference that asks for it is dropped rather than silently delivered elsewhere.
    "whatsapp": "",
}


def normalize_channels(channels: object) -> tuple[str, ...]:
    """Map any spelling onto the dispatcher's channel keys, de-duplicated and ordered.

    Unknown names (and whatsapp, which has no channel yet) are dropped.
    """
    if channels is None:
        return ()
    values = channels if isinstance(channels, list | tuple | set) else [channels]
    out: list[str] = []
    for raw in values:
        name = str(raw).strip().lower()
        name = CHANNEL_ALIASES.get(name, name)
        if name in CHANNEL_NAMES and name not in out:
            out.append(name)
    return tuple(sorted(out, key=CHANNEL_NAMES.index))


def slack_resolver(database: Database):  # type: ignore[no-untyped-def]
    async def resolve(tenant_id: uuid.UUID) -> SlackSettings | None:
        async with database.session(tenant_id) as session:
            return await load_slack_settings(session, tenant_id)

    return resolve


def teams_resolver(database: Database):  # type: ignore[no-untyped-def]
    async def resolve(tenant_id: uuid.UUID) -> TeamsSettings | None:
        async with database.session(tenant_id) as session:
            return await load_teams_settings(session, tenant_id)

    return resolve


def push_resolver(database: Database):  # type: ignore[no-untyped-def]
    async def resolve(tenant_id: uuid.UUID, user_id: uuid.UUID) -> Sequence[Subscription]:
        async with database.session(tenant_id) as session:
            return await load_push_subscriptions(session, user_id)

    return resolve


def push_pruner(database: Database):  # type: ignore[no-untyped-def]
    """Drop a subscription the push service reported as permanently gone (404/410)."""

    async def prune(tenant_id: uuid.UUID, subscription: Subscription) -> None:
        async with database.session(tenant_id) as session:
            row = (
                await session.execute(
                    select(PushSubscription).where(
                        PushSubscription.endpoint == subscription.endpoint
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                await delete_push_subscription(session, row.user_id, subscription.endpoint)

    return prune


def build_channels(settings: Settings, database: Database | None = None) -> dict[str, Channel]:
    """Every channel SPEC 7 lists for v1, keyed by the name used in user preferences."""
    db = database or get_database()
    return {
        "in_app": InAppChannel(),
        "email": EmailChannel(settings),
        "slack": SlackChannel(settings, resolver=slack_resolver(db)),
        "teams": TeamsChannel(settings, resolver=teams_resolver(db)),
        "push": PushChannel(settings, subscriptions=push_resolver(db), on_gone=push_pruner(db)),
    }


def build_dispatcher(
    settings: Settings,
    database: Database | None = None,
    *,
    channels: dict[str, Channel] | None = None,
) -> Dispatcher:
    return Dispatcher(channels or build_channels(settings, database), settings)
