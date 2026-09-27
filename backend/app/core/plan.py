"""Plan tiers and limit arithmetic (SPEC section 3). Pure logic.

Limits are integers; None means unlimited. plan_limits rows in the database are
seeded from PLAN_DEFAULTS by migration 0001 and read by services.plan.PlanService.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Resource(StrEnum):
    PROFILES = "profiles"
    SOURCE_REGIONS = "source_regions"
    INSTANT_ALERTS = "instant_alerts"
    AGENT_DRAFTS_PER_MONTH = "agent_drafts_per_month"


UNLIMITED: int | None = None

# Free: 1 profile, 1 source region, digest only, 0 drafts.
# Pro: 3 profiles, all sources, instant alerts, 10 agent drafts/month.
# Enterprise: unlimited.
PLAN_DEFAULTS: dict[Plan, dict[Resource, int | None]] = {
    Plan.FREE: {
        Resource.PROFILES: 1,
        Resource.SOURCE_REGIONS: 1,
        Resource.INSTANT_ALERTS: 0,
        Resource.AGENT_DRAFTS_PER_MONTH: 0,
    },
    Plan.PRO: {
        Resource.PROFILES: 3,
        Resource.SOURCE_REGIONS: UNLIMITED,
        Resource.INSTANT_ALERTS: 1,
        Resource.AGENT_DRAFTS_PER_MONTH: 10,
    },
    Plan.ENTERPRISE: {
        Resource.PROFILES: UNLIMITED,
        Resource.SOURCE_REGIONS: UNLIMITED,
        Resource.INSTANT_ALERTS: UNLIMITED,
        Resource.AGENT_DRAFTS_PER_MONTH: UNLIMITED,
    },
}

MONTHLY_SUFFIX = "_per_month"
LIFETIME_PERIOD = "lifetime"


def plan_limit_rows() -> list[dict[str, object]]:
    """Rows for the plan_limits table, in a stable order."""
    return [
        {"plan": plan.value, "resource": resource.value, "limit_value": limit}
        for plan, limits in PLAN_DEFAULTS.items()
        for resource, limit in limits.items()
    ]


def is_unlimited(limit: int | None) -> bool:
    return limit is None


def is_monthly(resource: str) -> bool:
    return resource.endswith(MONTHLY_SUFFIX)


def period_key(resource: str, at: datetime | None = None) -> str:
    """usage_ledger.period for a resource: 'YYYY-MM' (UTC) for monthly, else 'lifetime'."""
    if not is_monthly(resource):
        return LIFETIME_PERIOD
    moment = at or datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


@dataclass(frozen=True, slots=True)
class LimitCheck:
    resource: str
    limit: int | None
    used: int
    requested: int

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(self.limit - self.used, 0)

    @property
    def allowed(self) -> bool:
        if self.limit is None:
            return True
        return self.used + self.requested <= self.limit


def check_limit(resource: str, limit: int | None, used: int, requested: int = 1) -> LimitCheck:
    if requested < 0:
        raise ValueError("requested must be >= 0")
    if used < 0:
        raise ValueError("used must be >= 0")
    return LimitCheck(resource=resource, limit=limit, used=used, requested=requested)


def default_limit(plan: Plan | str, resource: Resource | str) -> int | None:
    """Static default for a plan/resource; unknown resources are treated as unlimited=0 (deny)."""
    plan_enum = Plan(plan)
    try:
        resource_enum = Resource(resource)
    except ValueError:
        return 0
    return PLAN_DEFAULTS[plan_enum][resource_enum]
