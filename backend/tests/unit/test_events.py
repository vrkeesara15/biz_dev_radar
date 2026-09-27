"""M2-09: in-process event bus."""

from app.services.events import (
    OPPORTUNITY_AMENDED,
    OPPORTUNITY_CREATED,
    Event,
    EventBus,
    Recorder,
    get_event_bus,
    set_event_bus,
)


async def test_publish_reaches_named_and_wildcard_subscribers_in_order() -> None:
    bus = EventBus()
    seen: list[str] = []
    recorder = Recorder()
    bus.subscribe(OPPORTUNITY_AMENDED, lambda e: seen.append("sync"))

    async def async_handler(event: Event) -> None:
        seen.append("async:" + event.payload["opportunity_id"])

    bus.subscribe(OPPORTUNITY_AMENDED, async_handler)
    bus.subscribe("*", recorder)
    event = await bus.publish(OPPORTUNITY_AMENDED, {"opportunity_id": "o1"})
    await bus.publish(OPPORTUNITY_CREATED, {"opportunity_id": "o2"})
    assert seen == ["sync", "async:o1"]
    assert [e.name for e in recorder.events] == [OPPORTUNITY_AMENDED, OPPORTUNITY_CREATED]
    assert recorder.named(OPPORTUNITY_CREATED)[0].payload == {"opportunity_id": "o2"}
    assert event.at.tzinfo is not None


async def test_failing_handler_does_not_break_publish_and_publisher_hook_runs() -> None:
    published: list[Event] = []

    async def celery_like(event: Event) -> None:
        published.append(event)

    bus = EventBus(publisher=celery_like)
    recorder = Recorder()

    def boom(event: Event) -> None:
        raise RuntimeError("subscriber bug")

    unsubscribe = bus.subscribe(OPPORTUNITY_AMENDED, boom)
    bus.subscribe(OPPORTUNITY_AMENDED, recorder)
    await bus.publish(OPPORTUNITY_AMENDED, {"opportunity_id": "o1"})
    assert len(recorder.events) == 1 and len(published) == 1
    unsubscribe()
    assert bus.handlers_for(OPPORTUNITY_AMENDED) == [recorder]


def test_process_bus_is_replaceable() -> None:
    original = get_event_bus()
    try:
        custom = EventBus()
        set_event_bus(custom)
        assert get_event_bus() is custom
    finally:
        set_event_bus(original)
