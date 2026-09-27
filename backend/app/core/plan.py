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


def effective_limit(
    plan: Plan | str,
    resource: Resource | str,
    *,
    is_internal: bool,
    configured: int | object | None = ...,
) -> int | None:
    """Limit that applies to a tenant: internal tenants are always unlimited (SPEC section 3).

    `configured` is the plan_limits value read from the database when available; when
    omitted the static PLAN_DEFAULTS apply.
    """
    if is_internal:
        return UNLIMITED
    if configured is ...:
        return default_limit(plan, resource)
    return configured  # type: ignore[return-value]


class PlanLimitExceeded(Exception):  # noqa: N818 - domain name mirrors the spec
    """Raised by services.plan.PlanService when a tenant would exceed a plan limit.

    Mapped to HTTP 402 by the API with the limit name in the body.
    """

    def __init__(self, plan: str, check: LimitCheck) -> None:
        self.plan = plan
        self.resource = check.resource
        self.limit = check.limit
        self.used = check.used
        self.requested = check.requested
        super().__init__(
            f"plan {plan}: limit '{check.resource}' is {check.limit} "
            f"(used {check.used}, requested {check.requested})"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "error": "plan_limit_exceeded",
            "limit": self.resource,
            "limit_value": self.limit,
            "used": self.used,
            "requested": self.requested,
            "plan": self.plan,
        }
