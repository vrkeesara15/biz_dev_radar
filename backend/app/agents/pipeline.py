"""The eight-agent pursuit pipeline as a step registry (SPEC 8).

    @register("collect", estimate=collector_estimate)
    async def collect(ctx: StepContext) -> CollectorOutput: ...

    specs, finish = plan_steps("all")        # implemented steps in order + how to finish
    specs, finish = plan_steps("pricing")    # one step
    specs, finish = plan_steps("all", gates_cleared=("gate1",))   # past the bid decision

Order is fixed (PIPELINE_ORDER). A step that no module has registered yet is
"unimplemented": `plan_steps("all")` returns every implemented step before the first
unimplemented one and asks the runner to finish `paused` naming it, so the second M5 pass
adds agents 4-8 by registering them (app.agents.<module> imported in `load_pipeline`).

Human gates (SPEC 8, 9): a step in GATES ends the plan unless its gate has been cleared.
Gate 1 sits after the bid/no-bid analyst -- nothing may be drafted before a human
approves the bid (`POST /pursuits/{id}/decision`, which clears the gate by recording
`pursuits.decision = 'bid'` and re-dispatching the same run). `gates_cleared` comes from
`app.services.pursuits.cleared_gates(pursuit)`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Collection
from dataclasses import dataclass

from app.agents.runner import Estimator, StepFn, StepSpec
from app.models.agents import RUN_DONE, RUN_PAUSED

STEP_COLLECT = "collect"
STEP_EXTRACT = "extract"
STEP_MATRIX = "matrix"
STEP_BID_NO_BID = "bid_no_bid"
STEP_OUTLINE = "outline"
STEP_DRAFT = "draft"
STEP_PRICING = "pricing"
STEP_RED_TEAM = "red_team"
STEP_ALL = "all"

PIPELINE_ORDER: tuple[str, ...] = (
    STEP_COLLECT,
    STEP_EXTRACT,
    STEP_MATRIX,
    STEP_BID_NO_BID,
    STEP_OUTLINE,
    STEP_DRAFT,
    STEP_PRICING,
    STEP_RED_TEAM,
)
# modules that register steps; importing them fills the registry
STEP_MODULES: tuple[str, ...] = (
    "app.agents.collector",
    "app.agents.extractor",
    "app.agents.matrix",
    "app.agents.bid_no_bid",
    "app.agents.outline",
    "app.agents.pricing",
)

# --- human gates (SPEC 8) -------------------------------------------------------------
GATE_1 = "gate1"
GATE_2 = "gate2"
# step -> the gate that closes AFTER it until a human clears it. Gate 2 (red-team review
# and approval) is added by M5-10 with the red_team step.
GATES: dict[str, str] = {STEP_BID_NO_BID: GATE_1}
GATE_REASONS: dict[str, str] = {
    GATE_1: "gate1: a bid/no-bid decision is required before drafting",
    GATE_2: "gate2: the draft package must be reviewed and approved",
}


@dataclass(frozen=True, slots=True)
class StepDef:
    name: str
    fn: StepFn
    estimate: Estimator | None = None
    input_ref: str | None = None

    def spec(self) -> StepSpec:
        return StepSpec(self.name, self.fn, input_ref=self.input_ref, estimate=self.estimate)


@dataclass(frozen=True, slots=True)
class Finish:
    status: str  # done | paused
    reason: str | None = None
    gate: str | None = None  # the human gate the run is waiting on (GATE_1 / GATE_2)


_REGISTRY: dict[str, StepDef] = {}
_loaded = False


def register(
    name: str, *, estimate: Estimator | None = None, input_ref: str | None = None
) -> Callable[[StepFn], StepFn]:
    if name not in PIPELINE_ORDER:
        raise ValueError(f"unknown pipeline step {name!r}; order is {PIPELINE_ORDER}")

    def deco(fn: StepFn) -> StepFn:
        _REGISTRY[name] = StepDef(name, fn, estimate=estimate, input_ref=input_ref)
        return fn

    return deco


def load_pipeline() -> None:
    global _loaded
    if _loaded:
        return
    for module in STEP_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:  # a later task's module: fine until it lands
            if exc.name != module:
                raise
    _loaded = True


def registered_steps() -> dict[str, StepDef]:
    load_pipeline()
    return dict(_REGISTRY)


def is_implemented(name: str) -> bool:
    return name in registered_steps()


def valid_step(name: str) -> bool:
    return name == STEP_ALL or name in PIPELINE_ORDER


def _gate_finish(name: str, gates_cleared: Collection[str]) -> Finish | None:
    """The paused finish a step's gate imposes, or None when there is nothing to wait for."""
    gate = GATES.get(name)
    if gate is None or gate in gates_cleared:
        return None
    return Finish(RUN_PAUSED, GATE_REASONS[gate], gate=gate)


def plan_steps(step: str, *, gates_cleared: Collection[str] = ()) -> tuple[list[StepSpec], Finish]:
    """StepSpecs to run for a request and how the run should finish afterwards.

    `gates_cleared` names the human gates a person has already passed for this pursuit;
    an uncleared gate stops the plan after the step it guards, whether the caller asked
    for that one step or for "all".
    """
    if not valid_step(step):
        raise ValueError(f"unknown step {step!r}; one of {(STEP_ALL, *PIPELINE_ORDER)}")
    steps = registered_steps()
    if step != STEP_ALL:
        if step not in steps:
            return [], Finish(RUN_PAUSED, f"step {step!r} is not implemented yet")
        spec = steps[step].spec()
        return [spec], _gate_finish(step, gates_cleared) or Finish(RUN_DONE)
    specs: list[StepSpec] = []
    for name in PIPELINE_ORDER:
        if name not in steps:
            return specs, Finish(RUN_PAUSED, f"step {name!r} is not implemented yet")
        specs.append(steps[name].spec())
        gate = _gate_finish(name, gates_cleared)
        if gate is not None:
            return specs, gate
    return specs, Finish(RUN_DONE)
