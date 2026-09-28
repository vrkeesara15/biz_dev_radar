"""Internal event bus: in-process pub/sub now, Celery-backed publisher hook later.

    bus = get_event_bus()
    bus.subscribe(OPPORTUNITY_AMENDED, handler)          # async def handler(event) -> None
    await bus.publish(OPPORTUNITY_AMENDED, {"opportunity_id": ...})

Handlers run in subscription order inside the publisher's task; a failing handler is
logged and never breaks ingestion. `publisher` (optional) receives every event after the
local handlers, which is where a Celery producer plugs in (M2-16). Tests use `Recorder`.
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

OPPORTUNITY_CREATED = "opportunity.created"
OPPORTUNITY_AMENDED = "opportunity.amended"
ADAPTER_FAILING = "adapter.failing"
# M4-06: a company profile changed in a way that invalidates its matches (its version
# bumped), so the open corpus is re-scored for it.
PROFILE_CHANGED = "profile.changed"
# M4-06: a newly scored match; the notification router (M4-14) subscribes to both.
MATCH_HIGH = "match.high"
MATCH_MEDIUM = "match.medium"
# M4-14 routes these two SPEC 7 rows; the agent pipeline (M5/M6) publishes them with
# {tenant_id, pursuit_id, assignee_user_id, title, ...}.
AGENT_DRAFT_READY = "agent.draft_ready"
AGENT_NEEDS_INPUT = "agent.needs_input"
# M4-14 / SPEC 7: SAM, DSC, certification or insurance expiring at 60/30/7 days; the
# reminder ladder (M6) publishes it with {tenant_id, kind, expires_on, ...}.
REGISTRATION_EXPIRING = "registration.expiring"
# SPEC 8 / 9 gates: a human recorded the bid/no-bid decision on a pursuit
PURSUIT_DECIDED = "pursuit.decided"


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    payload: dict[str, Any]
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # In-process only (never serialised to Celery): e.g. {"session": <AsyncSession>} so a
    # subscriber can read rows the publisher has not committed yet.
    context: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


Handler = Callable[[Event], Awaitable[None] | None]
Publisher = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self, publisher: Publisher | None = None) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.publisher = publisher

    def subscribe(self, name: str, handler: Handler) -> Callable[[], None]:
        """Register a handler for `name` ('*' = every event); returns an unsubscribe."""
        self._handlers[name].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(name, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def handlers_for(self, name: str) -> list[Handler]:
        return [*self._handlers.get(name, []), *self._handlers.get("*", [])]

    async def publish(
        self, name: str, payload: dict[str, Any], *, context: dict[str, Any] | None = None
    ) -> Event:
        event = Event(name=name, payload=payload, context=dict(context or {}))
        for handler in self.handlers_for(name):
            await _call(handler, event, name)
        if self.publisher is not None:
            await _call(self.publisher, event, name)
        return event


async def _call(target: Handler, event: Event, name: str) -> None:
    try:
        result = target(event)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        log.error("events.handler_failed", event_name=name, handler=repr(target), error=str(exc))


class Recorder:
    """Test subscriber: keeps every event it sees."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)

    def named(self, name: str) -> list[Event]:
        return [e for e in self.events if e.name == name]

    def clear(self) -> None:
        self.events.clear()


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus
