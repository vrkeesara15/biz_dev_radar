"""M0-05/M0-12: plan tiers and limit arithmetic (pure)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.core.plan import (
    PLAN_DEFAULTS,
    Plan,
    Resource,
    check_limit,
    default_limit,
    is_monthly,
    is_unlimited,
    period_key,
    plan_limit_rows,
)


def test_plan_defaults_match_spec() -> None:
    free, pro, ent = (
        PLAN_DEFAULTS[Plan.FREE],
        PLAN_DEFAULTS[Plan.PRO],
        PLAN_DEFAULTS[Plan.ENTERPRISE],
    )
    assert free == {
        Resource.PROFILES: 1,
        Resource.SOURCE_REGIONS: 1,
        Resource.INSTANT_ALERTS: 0,
        Resource.AGENT_DRAFTS_PER_MONTH: 0,
    }
    assert pro[Resource.PROFILES] == 3
    assert is_unlimited(pro[Resource.SOURCE_REGIONS])
    assert pro[Resource.INSTANT_ALERTS] == 1
    assert pro[Resource.AGENT_DRAFTS_PER_MONTH] == 10
    assert all(is_unlimited(v) for v in ent.values())


def test_plan_limit_rows_cover_every_plan_and_resource() -> None:
    rows = plan_limit_rows()
    assert len(rows) == len(Plan) * len(Resource)
    assert {(r["plan"], r["resource"]) for r in rows} == {
        (p.value, r.value) for p in Plan for r in Resource
    }


def test_period_key() -> None:
    assert is_monthly("agent_drafts_per_month")
    assert not is_monthly("profiles")
    assert period_key("profiles") == "lifetime"
    assert (
        period_key("agent_drafts_per_month", datetime(2026, 9, 30, 23, 0, tzinfo=UTC)) == "2026-09"
    )
    # 30 Sep 20:00 in UTC-5 is already 1 Oct in UTC: periods are UTC-based.
    late = datetime(2026, 9, 30, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert period_key("agent_drafts_per_month", late) == "2026-10"
    assert period_key("agent_drafts_per_month").count("-") == 1


def test_check_limit() -> None:
    ok = check_limit("profiles", 3, used=2)
    assert ok.allowed and ok.remaining == 1
    over = check_limit("profiles", 3, used=3)
    assert not over.allowed and over.remaining == 0
    assert check_limit("profiles", 3, used=1, requested=2).allowed
    assert not check_limit("profiles", 3, used=1, requested=3).allowed
    unlimited = check_limit("profiles", None, used=10_000)
    assert unlimited.allowed and unlimited.remaining is None
    assert check_limit("x", 0, used=0, requested=0).allowed
    with pytest.raises(ValueError):
        check_limit("x", 1, used=-1)
    with pytest.raises(ValueError):
        check_limit("x", 1, used=0, requested=-1)


def test_default_limit() -> None:
    assert default_limit("free", "profiles") == 1
    assert default_limit(Plan.PRO, Resource.AGENT_DRAFTS_PER_MONTH) == 10
    assert default_limit(Plan.ENTERPRISE, "profiles") is None
    assert default_limit(Plan.PRO, "unknown_resource") == 0
    with pytest.raises(ValueError):
        default_limit("gold", "profiles")


def test_effective_limit_internal_is_unlimited() -> None:
    from app.core.plan import effective_limit

    assert effective_limit(Plan.FREE, Resource.PROFILES, is_internal=True) is None
    assert effective_limit(Plan.FREE, Resource.PROFILES, is_internal=True, configured=1) is None
    assert effective_limit(Plan.FREE, Resource.PROFILES, is_internal=False) == 1
    assert effective_limit(Plan.FREE, Resource.PROFILES, is_internal=False, configured=7) == 7
    assert effective_limit(Plan.FREE, Resource.PROFILES, is_internal=False, configured=None) is None
