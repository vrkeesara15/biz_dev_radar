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
from app.models import PushSubscription, Tenant, User
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
from app.notify.whatsapp import WhatsAppChannel, WhatsAppTarget

CHANNEL_NAMES: tuple[str, ...] = ("in_app", "email", "slack", "teams", "push", "whatsapp")


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


def whatsapp_eligibility(database: Database):  # type: ignore[no-untyped-def]
    """SPEC 7: WhatsApp reaches Indian tenants, and only a verified number."""

    async def resolve(tenant_id: uuid.UUID, user_id: uuid.UUID) -> WhatsAppTarget:
        async with database.session(tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            user = await session.get(User, user_id)
            if tenant is None or user is None:
                return WhatsAppTarget()
            return WhatsAppTarget(
                phone_e164=user.phone_e164,
                phone_verified=user.phone_verified_at is not None,
                region=str(tenant.region),
            )

    return resolve


def build_channels(settings: Settings, database: Database | None = None) -> dict[str, Channel]:
    """Every channel SPEC 7 lists for v1, keyed by the name used in user preferences."""
    db = database or get_database()
    return {
        "in_app": InAppChannel(),
        "email": EmailChannel(settings),
        "slack": SlackChannel(settings, resolver=slack_resolver(db)),
        "teams": TeamsChannel(settings, resolver=teams_resolver(db)),
        "push": PushChannel(settings, subscriptions=push_resolver(db), on_gone=push_pruner(db)),
        "whatsapp": WhatsAppChannel(settings, eligibility=whatsapp_eligibility(db)),
    }


def build_dispatcher(
    settings: Settings,
    database: Database | None = None,
    *,
    channels: dict[str, Channel] | None = None,
) -> Dispatcher:
    return Dispatcher(channels or build_channels(settings, database), settings)
