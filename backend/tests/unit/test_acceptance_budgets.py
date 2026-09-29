"""M7-14: the two SPEC 12 latency boxes, as far as a schedule can prove them.

    "A new SAM.gov notice matching the internal profile appears in the app and
     in Slack within 60 minutes."
    "A new CPPP or GeM tender matching an India test profile appears within 6
     hours."

Nothing here observes a real portal: what these tests pin is the CEILING the
deployed schedule puts on the discovery half of each box -- the adapter's cron
plus the notification flush -- so a cadence change that would blow the budget
fails the build instead of being discovered on staging. The other half (that a
polled notice really does reach a match and an email/Slack message) is
`tests/integration/test_ingest_match_notify.py`. The box itself is only ticked
by watching a real notice, which is why docs/acceptance.md keeps it manual
(OQ-154).
"""

from __future__ import annotations

import pytest
from app.adapters.registry import load_builtin_adapters, registered
from app.celery_app import cron_to_crontab

# SPEC 12 "Acceptance criteria for MVP".
US_BUDGET_MINUTES = 60
IN_BUDGET_MINUTES = 6 * 60
# The queued alert is drained by `notify:flush` (crontab minute="*/5"), so the
# worst case is one poll interval plus one flush interval.
FLUSH_INTERVAL_MINUTES = 5


def _interval_minutes(cron: str) -> int:
    """Worst-case gap between two runs of a `*/n`-style cron, in minutes."""
    minute, hour, _dom, _month, _dow = cron.split()
    if minute.startswith("*/"):
        step = int(minute[2:])
        assert hour == "*", f"a sub-hourly cron must run every hour: {cron!r}"
        return step
    assert minute.isdigit(), f"unsupported cron for a budget claim: {cron!r}"
    if hour == "*":
        return 60
    if hour.startswith("*/"):
        return int(hour[2:]) * 60
    if hour.isdigit():
        return 24 * 60
    raise AssertionError(f"unsupported cron for a budget claim: {cron!r}")


@pytest.fixture(autouse=True)
def _adapters() -> None:
    load_builtin_adapters()


def test_the_sam_poll_plus_the_notify_flush_fits_the_60_minute_box() -> None:
    adapter = registered()["sam_opps"]
    cron_to_crontab(adapter.schedule)  # the string is a cron beat can actually run
    poll = _interval_minutes(adapter.schedule)
    assert poll + FLUSH_INTERVAL_MINUTES <= US_BUDGET_MINUTES, (
        f"sam_opps polls every {poll} min; with the {FLUSH_INTERVAL_MINUTES} min notify "
        f"flush that is over the SPEC 12 {US_BUDGET_MINUTES} min box"
    )


@pytest.mark.parametrize("source_id", ["cppp", "gem"])
def test_the_indian_portal_polls_fit_the_6_hour_box(source_id: str) -> None:
    adapter = registered()[source_id]
    cron_to_crontab(adapter.schedule)
    poll = _interval_minutes(adapter.schedule)
    assert poll + FLUSH_INTERVAL_MINUTES <= IN_BUDGET_MINUTES, (
        f"{source_id} polls every {poll} min; with the {FLUSH_INTERVAL_MINUTES} min notify "
        f"flush that is over the SPEC 12 {IN_BUDGET_MINUTES} min box"
    )


def test_every_enabled_adapter_has_a_cron_beat_can_run() -> None:
    """A box can only be argued from a schedule if every source HAS one."""
    for source_id, adapter in registered().items():
        if not getattr(adapter, "enabled", True):
            continue  # a documented stub has no beat on purpose
        assert cron_to_crontab(adapter.schedule) is not None, (
            f"{source_id} has no cron beat can run"
        )
