"""Event router (M4-14): which event goes to which channel, to whom, and when (SPEC 7).

    router = install_notification_router(settings, database, bus)
    # ... an event is published somewhere ...
    await router.drain()            # tests only

The table below IS SPEC 7's event table. Everything else in this module is the plumbing
that turns one bus event into `Dispatcher.dispatch(session, event, recipients)`:

  bus event               category            default channels              timing
  match.high              high_fit_match      in_app, email, slack, teams   instant*
  match.medium            digest              email                         digest
  opportunity.amended     amendment           in_app, email, slack          instant
    (only for a tenant that TRACKS the notice: it has a pursuit on it)
  agent.draft_ready       approval_request    in_app, email                 instant (assignee)
  agent.needs_input       agent_question      in_app, email                 instant (assignee)
  registration.expiring   registration_expiry email                         instant (owner)
  adapter.failing         adapter_failing     ops Slack webhook + ops email instant (platform)

  * "instant" respects quiet hours through `notify.scheduling.deliver_at`, which itself
    overrides them when the response is due in under 72 hours.

Per-user overrides: `user_notification_prefs.channels_by_event[<category>]` replaces the
default channel list (spelling normalised by `notify.registry.normalize_channels`), and
`min_score_instant` / `min_score_digest` drop a match a user does not care about.

Alert rules (M4-08) override BOTH for a match: when any enabled rule accepts the match,
its channels and its mode win, and a rule that names a user addresses that user alone.

`adapter.failing` has no tenant and no user, so it never touches `notifications`; it goes
straight to the platform's own Slack webhook and ops mailbox (OQ-101).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import Database, get_database
from app.core.preferences import NotificationEvent as Category
from app.core.roles import Role
from app.models import (
    Membership,
    Opportunity,
    Pursuit,
    User,
    UserNotificationPrefs,
)
from app.notify.core import Dispatcher, NotificationEvent, Recipient
from app.notify.email import OutboundEmail, build_email_provider
from app.notify.registry import build_dispatcher, normalize_channels
from app.notify.render import email_context, render_email
from app.notify.scheduling import SchedulePrefs, deliver_at, next_digest_at
from app.notify.unsubscribe import load_unsubscribed
from app.services.events import (
    ADAPTER_FAILING,
    AGENT_DRAFT_READY,
    AGENT_NEEDS_INPUT,
    MATCH_HIGH,
    MATCH_MEDIUM,
    OPPORTUNITY_AMENDED,
    REGISTRATION_EXPIRING,
    Event,
    EventBus,
)
from app.services.matching.alerts import AlertDecision, evaluate_rules

log = structlog.get_logger(__name__)

INSTANT = "instant"
DIGEST = "digest"
OPS_EVENT = "adapter_failing"

# audiences
MATCH_ROLES: tuple[Role, ...] = (Role.TENANT_OWNER, Role.BID_MANAGER)
TRACKED_ROLES: tuple[Role, ...] = (Role.TENANT_OWNER, Role.BID_MANAGER)
OWNER_ROLES: tuple[Role, ...] = (Role.TENANT_OWNER,)


@dataclass(frozen=True, slots=True)
class Route:
    """One row of SPEC 7's event table."""

    event: str  # the bus event name
    category: str  # core.preferences.NotificationEvent value (the prefs / template key)
    channels: tuple[str, ...]
    mode: str  # instant | digest
    audience: str  # match | tracked | assignee | owner | ops
    roles: tuple[Role, ...] = ()


ROUTES: dict[str, Route] = {
    MATCH_HIGH: Route(
        event=MATCH_HIGH,
        category=Category.HIGH_FIT_MATCH.value,
        channels=("in_app", "email", "slack", "teams"),
        mode=INSTANT,
        audience="match",
        roles=MATCH_ROLES,
    ),
    MATCH_MEDIUM: Route(
        event=MATCH_MEDIUM,
        category=Category.DIGEST.value,
        channels=("email",),
        mode=DIGEST,
        audience="match",
        roles=MATCH_ROLES,
    ),
    OPPORTUNITY_AMENDED: Route(
        event=OPPORTUNITY_AMENDED,
        category=Category.AMENDMENT.value,
        channels=("in_app", "email", "slack"),
        mode=INSTANT,
        audience="tracked",
        roles=TRACKED_ROLES,
    ),
    AGENT_DRAFT_READY: Route(
        event=AGENT_DRAFT_READY,
        category=Category.APPROVAL_REQUEST.value,
        channels=("in_app", "email"),
        mode=INSTANT,
        audience="assignee",
    ),
    AGENT_NEEDS_INPUT: Route(
        event=AGENT_NEEDS_INPUT,
        category=Category.AGENT_QUESTION.value,
        channels=("in_app", "email"),
        mode=INSTANT,
        audience="assignee",
    ),
    REGISTRATION_EXPIRING: Route(
        event=REGISTRATION_EXPIRING,
        category=Category.REGISTRATION_EXPIRY.value,
        channels=("email",),
        mode=INSTANT,
        audience="owner",
        roles=OWNER_ROLES,
    ),
    ADAPTER_FAILING: Route(
        event=ADAPTER_FAILING,
        category=OPS_EVENT,
        channels=("slack", "email"),
        mode=INSTANT,
        audience="ops",
    ),
}


@dataclass(slots=True)
class RouteRun:
    """What one routed event produced (returned for tests and logs)."""

    event: str
    tenants: int = 0
    recipients: int = 0
    notifications: int = 0
    deliveries: int = 0
    duplicates: int = 0
    skipped: list[str] = field(default_factory=list)
    ops: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        # `event_name`, not `event`: structlog reserves `event` for the message itself.
        return {
            "event_name": self.event,
            "tenants": self.tenants,
            "recipients": self.recipients,
            "notifications": self.notifications,
            "deliveries": self.deliveries,
            "duplicates": self.duplicates,
            "skipped": list(self.skipped),
            "ops": list(self.ops),
        }


# --- pure helpers (table-driven unit tests live on these) -------------------------------


def route_for(event_name: str) -> Route | None:
    return ROUTES.get(event_name)


def channels_for(
    route: Route,
    prefs: UserNotificationPrefs | None,
    decisions: Sequence[AlertDecision] = (),
) -> tuple[str, ...]:
    """Alert rules win, then the user's own per-event choice, then the SPEC 7 default."""
    if decisions:
        chosen: list[str] = []
        for decision in decisions:
            for channel in decision.channels:
                if channel not in chosen:
                    chosen.append(channel)
        if chosen:
            return normalize_channels(chosen)
    if prefs is not None:
        override = (prefs.channels_by_event or {}).get(route.category)
        if override is not None:
            return normalize_channels(override)
    return normalize_channels(route.channels)


def mode_for(route: Route, decisions: Sequence[AlertDecision] = ()) -> str:
    """A rule that asks for an instant alert beats the band's default timing."""
    if decisions:
        return INSTANT if any(d.instant for d in decisions) else DIGEST
    return route.mode


def score_allows(route: Route, prefs: UserNotificationPrefs | None, score: Any) -> bool:
    """SPEC 7 "minimum score" per user, against the band's own threshold."""
    if prefs is None or score is None:
        return True
    floor = prefs.min_score_instant if route.event == MATCH_HIGH else prefs.min_score_digest
    try:
        return Decimal(str(score)) >= Decimal(floor)
    except (ArithmeticError, ValueError):  # pragma: no cover - a malformed payload
        return True


# --- the router ---------------------------------------------------------------------------


class NotificationRouter:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        database: Database | None = None,
        dispatcher: Dispatcher | None = None,
        now: datetime | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.database = database or get_database()
        self.dispatcher = dispatcher or build_dispatcher(self.settings, self.database)
        self._now = now
        self._http = http_client
        self._pending: set[asyncio.Task[Any]] = set()
        self.runs: list[RouteRun] = []

    @property
    def clock(self) -> datetime:
        return self._now or datetime.now(UTC)

    # -- subscription -----------------------------------------------------------------

    def subscribe(self, bus: EventBus) -> NotificationRouter:
        for name in ROUTES:
            bus.subscribe(name, self.on_event)
        return self

    async def on_event(self, event: Event) -> None:
        """Route now when the publisher has committed, else on its `after_commit`."""
        session = event.context.get("session")
        if session is None:
            await self._guarded(self.route(event))
            return
        loop = asyncio.get_running_loop()
        from sqlalchemy import event as sa_event

        def _after_commit(_sync_session: Any) -> None:
            loop.call_soon(self._spawn, event)

        sa_event.listen(session.sync_session, "after_commit", _after_commit, once=True)

    def _spawn(self, event: Event) -> None:
        task = asyncio.get_running_loop().create_task(self._guarded(self.route(event)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _guarded(self, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            await coro
        except Exception as exc:  # a notification failure must never break the publisher
            log.error("notify.route_failed", error=str(exc)[:400])

    async def drain(self, *, passes: int = 5) -> None:
        for _ in range(passes):
            await asyncio.sleep(0)
            if self._pending:
                await asyncio.gather(*list(self._pending))

    # -- routing ----------------------------------------------------------------------

    async def route(self, event: Event) -> RouteRun:
        route = route_for(event.name)
        run = RouteRun(event=event.name)
        if route is None:  # pragma: no cover - we only subscribe to what we route
            return run
        if route.audience == "ops":
            await self._route_ops(event, route, run)
        elif route.audience == "tracked":
            await self._route_tracked(event, route, run)
        else:
            tenant_id = _uuid(event.payload.get("tenant_id"))
            if tenant_id is None:
                run.skipped.append("no tenant_id in the payload")
            else:
                await self._route_tenant(event, route, tenant_id, run)
        self.runs.append(run)
        log.info("notify.routed", **run.as_dict())
        return run

    async def _route_tracked(self, event: Event, route: Route, run: RouteRun) -> None:
        """An amendment reaches only the tenants that actually track the notice."""
        opportunity_id = _uuid(event.payload.get("opportunity_id"))
        if opportunity_id is None:
            run.skipped.append("no opportunity_id in the payload")
            return
        async with self.database.owner_session() as session:
            tenants = (
                (
                    await session.execute(
                        select(Pursuit.tenant_id)
                        .where(Pursuit.opportunity_id == opportunity_id)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
        if not tenants:
            run.skipped.append("nobody tracks this opportunity")
            return
        for tenant_id in tenants:
            await self._route_tenant(event, route, tenant_id, run)

    async def _route_tenant(
        self, event: Event, route: Route, tenant_id: uuid.UUID, run: RouteRun
    ) -> None:
        now = self.clock
        async with self.database.session(tenant_id) as session:
            payload, due_at = await self._payload(session, event, route)
            decisions = await self._decisions(session, event, route, tenant_id)
            users = await self._audience(session, event, route, decisions)
            if not users:
                run.skipped.append(f"{tenant_id}: no recipient")
                return
            run.tenants += 1
            recipients: list[Recipient] = []
            for user, prefs in users:
                if not score_allows(route, prefs, event.payload.get("score")):
                    run.skipped.append(f"{user.id}: below their minimum score")
                    continue
                channels = channels_for(route, prefs, decisions)
                if not channels:
                    run.skipped.append(f"{user.id}: every channel muted")
                    continue
                schedule = SchedulePrefs.from_row(prefs) if prefs is not None else SchedulePrefs()
                scheduled_for = self._scheduled_for(route, decisions, schedule, now, due_at)
                recipients.append(
                    Recipient(
                        user_id=user.id,
                        channels=channels,
                        email=user.email,
                        name=user.name,
                        tz=schedule.tz,
                        scheduled_for=scheduled_for,
                        unsubscribed=await load_unsubscribed(session, user.id),
                    )
                )
            if not recipients:
                return
            run.recipients += len(recipients)
            notification = NotificationEvent(
                event_type=route.category,
                tenant_id=tenant_id,
                opportunity_id=_uuid(event.payload.get("opportunity_id")),
                pursuit_id=_uuid(event.payload.get("pursuit_id")),
                version=int(event.payload.get("version") or 1),
                payload=payload,
                occurred_at=event.at,
                response_due_at=due_at,
            )
            result = await self.dispatcher.dispatch(session, notification, recipients)
            run.notifications += len(result.notifications)
            run.deliveries += len(result.deliveries)
            run.duplicates += len(result.duplicates)

    def _scheduled_for(
        self,
        route: Route,
        decisions: Sequence[AlertDecision],
        schedule: SchedulePrefs,
        now: datetime,
        due_at: datetime | None,
    ) -> datetime | None:
        """Quiet hours for an instant alert; the next digest slot for a digest one."""
        if mode_for(route, decisions) == DIGEST:
            return next_digest_at(schedule, now)
        plan = deliver_at(schedule, now=now, response_due_at=due_at)
        return plan.send_at if plan.deferred else None

    async def _decisions(
        self, session: AsyncSession, event: Event, route: Route, tenant_id: uuid.UUID
    ) -> list[AlertDecision]:
        """M4-08 alert rules, for match events only."""
        if route.audience != "match":
            return []
        opportunity_id = _uuid(event.payload.get("opportunity_id"))
        profile_id = _uuid(event.payload.get("profile_id"))
        if opportunity_id is None or profile_id is None:
            return []
        row = await session.get(Opportunity, opportunity_id)
        if row is None:  # pragma: no cover - opportunities are global and never deleted here
            return []
        return await evaluate_rules(
            session,
            opportunity=row,
            profile_id=profile_id,
            score=event.payload.get("score") or 0,
            now=self.clock,
        )

    async def _audience(
        self,
        session: AsyncSession,
        event: Event,
        route: Route,
        decisions: Sequence[AlertDecision],
    ) -> list[tuple[User, UserNotificationPrefs | None]]:
        named: list[uuid.UUID] = []
        if route.audience == "assignee":
            for key in ("assignee_user_id", "user_id", "owner_user_id"):
                candidate = _uuid(event.payload.get(key))
                if candidate is not None:
                    named.append(candidate)
                    break
        elif decisions:
            named = [d.user_id for d in decisions if d.user_id is not None]
        if named:
            return await self._users_by_id(session, named)
        return await self._users_by_role(session, route.roles or MATCH_ROLES)

    async def _users_by_id(
        self, session: AsyncSession, ids: Sequence[uuid.UUID]
    ) -> list[tuple[User, UserNotificationPrefs | None]]:
        rows = (
            await session.execute(
                select(User, UserNotificationPrefs)
                .join(Membership, Membership.user_id == User.id)
                .outerjoin(UserNotificationPrefs, UserNotificationPrefs.user_id == User.id)
                .where(User.id.in_(list(dict.fromkeys(ids))))
                .order_by(User.email)
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    async def _users_by_role(
        self, session: AsyncSession, roles: Sequence[Role]
    ) -> list[tuple[User, UserNotificationPrefs | None]]:
        rows = (
            await session.execute(
                select(User, UserNotificationPrefs)
                .join(Membership, Membership.user_id == User.id)
                .outerjoin(UserNotificationPrefs, UserNotificationPrefs.user_id == User.id)
                .where(Membership.role.in_(list(roles)))
                .order_by(User.email)
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    async def _payload(
        self, session: AsyncSession, event: Event, route: Route
    ) -> tuple[dict[str, Any], datetime | None]:
        """The notification payload: the event plus what the templates need of the notice."""
        payload = {k: v for k, v in event.payload.items() if k != "tenant_id"}
        opportunity_id = _uuid(event.payload.get("opportunity_id"))
        if opportunity_id is None:
            return payload, None
        row = await session.get(Opportunity, opportunity_id)
        if row is None:  # pragma: no cover
            return payload, None
        payload.setdefault("title", row.title)
        payload.setdefault("buyer", " / ".join(row.buyer_hierarchy) or row.buyer_org)
        payload.setdefault("summary", row.summary_ai)
        payload.setdefault("source_tz", row.source_tz)
        if row.estimated_value_max is not None or row.estimated_value_min is not None:
            payload.setdefault(
                "value_amount", str(row.estimated_value_max or row.estimated_value_min)
            )
            payload.setdefault("value_currency", row.currency)
        if row.response_due_at is not None:
            payload.setdefault("response_due_at", row.response_due_at.isoformat())
        return payload, row.response_due_at

    # -- the ops channel ---------------------------------------------------------------

    async def _route_ops(self, event: Event, route: Route, run: RouteRun) -> None:
        """SPEC 7 last row: a failing adapter pages the PLATFORM, not a tenant."""
        payload = dict(event.payload)
        source = str(payload.get("source_id") or "unknown source")
        failures = payload.get("consecutive_failures")
        headline = f"Adapter {source} is failing ({failures} consecutive runs)"
        detail = str(payload.get("message") or payload.get("health") or "")
        if self.settings.ops_slack_webhook_url:
            await self._ops_slack(headline, detail, payload, run)
        else:
            run.skipped.append("no OPS_SLACK_WEBHOOK_URL")
        if self.settings.ops_email:
            await self._ops_email(headline, payload, run)
        else:
            run.skipped.append("no OPS_EMAIL")

    async def _ops_slack(
        self, headline: str, detail: str, payload: dict[str, Any], run: RouteRun
    ) -> None:
        body = {
            "text": headline,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": headline}},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": detail[:2000] or "_no message_"},
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"run `{payload.get('run_id')}` · health "
                            f"`{payload.get('health')}`",
                        }
                    ],
                },
            ],
        }
        client = self._http or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(self.settings.ops_slack_webhook_url, json=body)
            if response.status_code >= 400:
                run.skipped.append(f"ops slack HTTP {response.status_code}")
            else:
                run.ops.append("slack")
        except httpx.HTTPError as exc:
            run.skipped.append(f"ops slack {type(exc).__name__}")
        finally:
            if self._http is None:
                await client.aclose()

    async def _ops_email(self, headline: str, payload: dict[str, Any], run: RouteRun) -> None:
        context = email_context(
            {**payload, "title": headline},
            event_type=OPS_EVENT,
            settings=self.settings,
            tenant_id=uuid.UUID(int=0),
            user_id=uuid.UUID(int=0),
            now=self.clock,
        )
        rendered = render_email(OPS_EVENT, context)
        provider = build_email_provider(self.settings)
        result = await provider.send(
            OutboundEmail(
                to=self.settings.ops_email,
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
                from_email=self.settings.email_from,
                from_name=self.settings.email_from_name,
                headers={"X-BidRadar-Event": OPS_EVENT},
            )
        )
        if result.ok:
            run.ops.append("email")
        else:
            run.skipped.append(f"ops email {result.error}")


def _uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:  # pragma: no cover - a malformed payload
        return None


def install_notification_router(
    settings: Settings,
    database: Database,
    bus: EventBus,
    *,
    dispatcher: Dispatcher | None = None,
) -> NotificationRouter:
    """Subscribe SPEC 7's routing table to this process's event bus."""
    return NotificationRouter(
        settings=settings, database=database, dispatcher=dispatcher
    ).subscribe(bus)


__all__ = [
    "DIGEST",
    "INSTANT",
    "ROUTES",
    "NotificationRouter",
    "Route",
    "RouteRun",
    "channels_for",
    "install_notification_router",
    "mode_for",
    "route_for",
    "score_allows",
]
