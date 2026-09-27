"""Cost guard arithmetic (SPEC 8: per-tenant monthly budget and per-pursuit cap, default
USD 15; "stop and ask when exceeded"). Pure: the runner supplies what was spent and what
the next step is projected to cost, this module answers allowed / not allowed and why.

    decision = check_budget(spent_month, budget, projected)        # tenant month
    decision = check_pursuit_cap(spent_pursuit, cap, projected)    # one pursuit
    first_block([decision_a, decision_b])                          # None when all allowed

Projection is deliberately rough (input chars / 4 tokens x model price, plus an output
allowance): it only has to stop a step BEFORE it spends, the ledger keeps the truth after.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.core.llm_cost import ModelPrice, estimate_cost

SCOPE_TENANT_MONTH = "tenant_month"
SCOPE_PURSUIT = "pursuit"
CHARS_PER_TOKEN = 4
DEFAULT_PURSUIT_CAP_USD = Decimal("15")
_CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    scope: str  # tenant_month | pursuit
    spent: Decimal
    projected: Decimal
    limit: Decimal | None  # None = unlimited
    reason: str | None = None

    @property
    def remaining(self) -> Decimal | None:
        if self.limit is None:
            return None
        return max(self.limit - self.spent, Decimal(0))


@dataclass(frozen=True, slots=True)
class StepEstimate:
    """What a step expects to send and receive; `calls` multiplies both."""

    model: str
    input_chars: int = 0
    output_tokens: int = 0
    calls: int = 1
    cache_read_chars: int = 0


def _money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def estimate_tokens(chars: int) -> int:
    """~4 characters per token, rounded up; never negative."""
    if chars <= 0:
        return 0
    return math.ceil(chars / CHARS_PER_TOKEN)


def project_cost(estimate: StepEstimate, prices: Mapping[str, ModelPrice]) -> Decimal:
    """USD a step is expected to spend, from the price table (unknown model raises)."""
    calls = max(estimate.calls, 1)
    return estimate_cost(
        estimate.model,
        prices,
        tokens_in=estimate_tokens(estimate.input_chars) * calls,
        tokens_out=max(estimate.output_tokens, 0) * calls,
        cache_read_tokens=estimate_tokens(estimate.cache_read_chars) * calls,
    )


def _check(
    scope: str, spent: Decimal, limit: Decimal | None, projected: Decimal, label: str
) -> BudgetDecision:
    if spent < 0 or projected < 0:
        raise ValueError("spent and projected cost must be >= 0")
    if limit is None:
        return BudgetDecision(True, scope, spent, projected, None)
    if limit < 0:
        raise ValueError("limit must be >= 0 or None")
    if spent + projected <= limit:
        return BudgetDecision(True, scope, spent, projected, limit)
    reason = (
        f"{label}: spent {_money(spent)} USD + projected {_money(projected)} USD "
        f"exceeds the limit of {_money(limit)} USD"
    )
    return BudgetDecision(False, scope, spent, projected, limit, reason)


def check_budget(
    spent_month: Decimal, budget: Decimal | int | None, projected_step_cost: Decimal
) -> BudgetDecision:
    """Tenant monthly budget (plan_limits agent_budget_usd_month; None = unlimited)."""
    limit = None if budget is None else Decimal(budget)
    return _check(
        SCOPE_TENANT_MONTH, spent_month, limit, projected_step_cost, "tenant monthly budget"
    )


def check_pursuit_cap(
    spent_pursuit: Decimal, cap: Decimal | int | None, projected_step_cost: Decimal
) -> BudgetDecision:
    """Per-pursuit cap (tenant default, raised per pursuit by approve-budget)."""
    limit = None if cap is None else Decimal(cap)
    return _check(SCOPE_PURSUIT, spent_pursuit, limit, projected_step_cost, "pursuit cap")


def first_block(decisions: Iterable[BudgetDecision]) -> BudgetDecision | None:
    """The first decision that blocks, or None when every scope allows the step."""
    for decision in decisions:
        if not decision.allowed:
            return decision
    return None


def raise_cap(current: Decimal | int | None, additional: Decimal | int) -> Decimal:
    """New cap after an approval; an unlimited cap stays unlimited only if it was None
    (callers keep None separately), so this always returns a number."""
    extra = Decimal(str(additional))
    if extra <= 0:
        raise ValueError("additional budget must be > 0")
    base = Decimal(0) if current is None else Decimal(current)
    return _money(base + extra)
