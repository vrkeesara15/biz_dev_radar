"""M5-02: pure cost-guard arithmetic (tenant monthly budget, per-pursuit cap, projection)."""

from decimal import Decimal

import pytest
from app.core.cost_guard import (
    SCOPE_PURSUIT,
    SCOPE_TENANT_MONTH,
    BudgetDecision,
    StepEstimate,
    check_budget,
    check_pursuit_cap,
    estimate_tokens,
    first_block,
    project_cost,
    raise_cap,
)
from app.core.llm_cost import ModelPrice
from app.core.plan import PLAN_DEFAULTS, Plan, Resource, is_monthly, period_key

PRICES = {
    "opus-x": ModelPrice(
        input=Decimal(5), output=Decimal(25), cache_read=Decimal("0.5"), cache_write=Decimal(6)
    )
}


def test_plan_defaults_carry_the_agent_budget() -> None:
    assert PLAN_DEFAULTS[Plan.FREE][Resource.AGENT_BUDGET_USD_MONTH] == 0
    assert PLAN_DEFAULTS[Plan.PRO][Resource.AGENT_BUDGET_USD_MONTH] == 50
    assert PLAN_DEFAULTS[Plan.ENTERPRISE][Resource.AGENT_BUDGET_USD_MONTH] is None
    assert is_monthly(Resource.AGENT_BUDGET_USD_MONTH)
    assert period_key(Resource.AGENT_BUDGET_USD_MONTH).count("-") == 1


def test_estimate_tokens_rounds_up() -> None:
    assert estimate_tokens(0) == 0 and estimate_tokens(-5) == 0
    assert estimate_tokens(4) == 1 and estimate_tokens(5) == 2 and estimate_tokens(4000) == 1000


def test_project_cost_uses_the_price_table() -> None:
    # 40,000 chars -> 10,000 input tokens x $5/M = 0.05; 400 output x $25/M = 0.01
    est = StepEstimate(model="opus-x", input_chars=40_000, output_tokens=400)
    assert project_cost(est, PRICES) == Decimal("0.060000")
    # three calls triple it; cached chars are priced at the cache-read rate
    assert project_cost(StepEstimate("opus-x", 40_000, 400, calls=3), PRICES) == Decimal("0.18")
    cached = StepEstimate("opus-x", input_chars=0, output_tokens=0, cache_read_chars=40_000)
    assert project_cost(cached, PRICES) == Decimal("0.005")
    with pytest.raises(KeyError):
        project_cost(StepEstimate(model="unknown"), PRICES)


def test_budget_allows_up_to_the_limit_and_blocks_past_it() -> None:
    ok = check_budget(Decimal("49.50"), 50, Decimal("0.50"))
    assert ok.allowed and ok.scope == SCOPE_TENANT_MONTH and ok.remaining == Decimal("0.50")
    blocked = check_budget(Decimal("49.50"), 50, Decimal("0.51"))
    assert not blocked.allowed and blocked.reason
    assert "tenant monthly budget" in blocked.reason and "50.00 USD" in blocked.reason
    assert "49.50" in blocked.reason and "0.51" in blocked.reason
    # a zero budget (free plan) blocks even a free step once anything was spent, and blocks
    # the first paid step immediately
    assert check_budget(Decimal(0), 0, Decimal(0)).allowed
    assert not check_budget(Decimal(0), 0, Decimal("0.000001")).allowed
    # unlimited
    unlimited = check_budget(Decimal(10_000), None, Decimal(500))
    assert unlimited.allowed and unlimited.limit is None and unlimited.remaining is None


def test_pursuit_cap_default_15_crossed_mid_run() -> None:
    steps = [Decimal("4"), Decimal("6"), Decimal("6")]
    spent = Decimal(0)
    decisions: list[BudgetDecision] = []
    for projected in steps:
        d = check_pursuit_cap(spent, Decimal(15), projected)
        decisions.append(d)
        if d.allowed:
            spent += projected
    assert [d.allowed for d in decisions] == [True, True, False]
    assert decisions[2].scope == SCOPE_PURSUIT and decisions[2].reason
    assert "pursuit cap" in decisions[2].reason and decisions[2].remaining == Decimal(5)
    # raising the cap by 5 lets the last step through
    assert raise_cap(Decimal(15), 5) == Decimal("20.00")
    assert check_pursuit_cap(spent, raise_cap(15, 5), Decimal(6)).allowed
    with pytest.raises(ValueError):
        raise_cap(15, 0)
    assert raise_cap(None, Decimal("2.5")) == Decimal("2.50")


def test_first_block_and_validation() -> None:
    a = check_budget(Decimal(1), 50, Decimal(1))
    b = check_pursuit_cap(Decimal(14), 15, Decimal(2))
    c = check_budget(Decimal(60), 50, Decimal(0))
    assert first_block([a]) is None
    assert first_block([a, b, c]) is b
    with pytest.raises(ValueError):
        check_budget(Decimal(-1), 50, Decimal(0))
    with pytest.raises(ValueError):
        check_pursuit_cap(Decimal(0), -1, Decimal(0))
