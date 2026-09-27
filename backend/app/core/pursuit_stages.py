"""Pipeline stages and the rules that guard a move between them (SPEC 9). Pure, no I/O.

    ok, reason = can_transition("bid_decision", "drafting", TransitionContext())
    ok        -> False
    reason    -> "drafting requires a bid decision (Gate 1)"

SPEC 9 pipeline: Identified -> Qualifying -> Bid decision -> Drafting -> In review ->
Final approval -> Submitted -> Awarded / Lost / Cancelled / No-bid.

Rules (the board drags against these; the API answers 409 with `reason`):

* forward moves walk the linear ladder ONE step at a time — skipping a stage is refused
  by name so the UI can say which stage is missing;
* Drafting needs Gate 1: `pursuits.decision == 'bid'` (SPEC 9 "cannot enter Drafting
  without Gate 1 approval"). The gate is checked wherever Drafting is entered from;
* Submitted is only reachable from Final approval (it is the last rung of the ladder);
* Awarded / Lost are only reachable from Submitted — nothing is won before it is sent;
* No-bid only from Qualifying or Bid decision: once drafting has started the pursuit is
  cancelled (with a reason), not silently re-labelled as never-considered;
* Cancelled from any live stage;
* moving BACKWARDS, and reopening a terminal pursuit, are bid-manager (or tenant-owner)
  moves: a writer cannot undo an approval gate by dragging a card.

`can_transition` is total: every (from, to) pair answers, and an unknown stage name is a
refusal rather than an exception, so a stale client can never crash the route.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.roles import Role

STAGE_IDENTIFIED = "identified"
STAGE_QUALIFYING = "qualifying"
STAGE_BID_DECISION = "bid_decision"
STAGE_DRAFTING = "drafting"
STAGE_IN_REVIEW = "in_review"
STAGE_FINAL_APPROVAL = "final_approval"
STAGE_SUBMITTED = "submitted"
STAGE_AWARDED = "awarded"
STAGE_LOST = "lost"
STAGE_CANCELLED = "cancelled"
STAGE_NO_BID = "no_bid"

# The linear ladder, in order. Everything else is an outcome.
LADDER: tuple[str, ...] = (
    STAGE_IDENTIFIED,
    STAGE_QUALIFYING,
    STAGE_BID_DECISION,
    STAGE_DRAFTING,
    STAGE_IN_REVIEW,
    STAGE_FINAL_APPROVAL,
    STAGE_SUBMITTED,
)
TERMINAL_STAGES: tuple[str, ...] = (STAGE_AWARDED, STAGE_LOST, STAGE_CANCELLED, STAGE_NO_BID)
STAGES: tuple[str, ...] = (*LADDER, *TERMINAL_STAGES)

# Stages that still need attention: the reminder ladder keeps escalating until a pursuit
# leaves this set (SPEC 9 "until someone marks it submitted, passed or cancelled").
OPEN_STAGES: tuple[str, ...] = tuple(s for s in LADDER if s != STAGE_SUBMITTED)

DECISION_BID = "bid"
DECISION_NO_BID = "no_bid"

# SPEC 3: bid managers (and the tenant owner) move, assign and decide.
MANAGER_ROLES: frozenset[Role] = frozenset({Role.TENANT_OWNER, Role.BID_MANAGER})

DEFAULT_STAGE = STAGE_IDENTIFIED
# internal deadline = the real one minus this many hours (SPEC 9)
INTERNAL_DUE_OFFSET_HOURS = 48

GATE_1_REASON = "drafting requires a bid decision (Gate 1)"


@dataclass(frozen=True, slots=True)
class TransitionContext:
    """What the rules need to know about the pursuit and who is moving it."""

    decision: str | None = None  # pursuits.decision: bid | no_bid | None (Gate 1)
    role: Role = Role.BID_MANAGER


def is_stage(value: str) -> bool:
    return value in STAGES


def is_terminal(stage: str) -> bool:
    return stage in TERMINAL_STAGES


def is_open(stage: str) -> bool:
    """True while the pursuit still needs work (reminders keep firing)."""
    return stage in OPEN_STAGES


def next_stage(stage: str) -> str | None:
    """The next rung of the ladder, or None at the end / off the ladder."""
    if stage not in LADDER:
        return None
    index = LADDER.index(stage)
    return LADDER[index + 1] if index + 1 < len(LADDER) else None


def _manager(role: Role) -> bool:
    return role in MANAGER_ROLES


def can_transition(current: str, target: str, ctx: TransitionContext) -> tuple[bool, str]:
    """Whether `current -> target` is allowed, and why not when it is refused.

    Returns (ok, reason); `reason` is empty exactly when `ok` is True.
    """
    if not is_stage(current):
        return False, f"unknown stage {current!r}"
    if not is_stage(target):
        return False, f"unknown stage {target!r}; one of {', '.join(STAGES)}"
    if current == target:
        return True, ""

    if target == STAGE_CANCELLED:
        if is_terminal(current):
            return False, f"a {current} pursuit cannot be cancelled"
        return True, ""

    if target == STAGE_NO_BID:
        if current in (STAGE_QUALIFYING, STAGE_BID_DECISION):
            return True, ""
        return False, (
            "no_bid is only available from qualifying or bid_decision; "
            "cancel the pursuit with a reason instead"
        )

    if is_terminal(current):  # reopening an outcome
        if not _manager(ctx.role):
            return False, f"only a bid manager may reopen a {current} pursuit"
        if target == STAGE_DRAFTING and ctx.decision != DECISION_BID:
            return False, GATE_1_REASON
        return True, ""

    if target in (STAGE_AWARDED, STAGE_LOST):
        if current != STAGE_SUBMITTED:
            return False, f"only a submitted pursuit can be marked {target}"
        return True, ""

    # both ends are on the ladder from here on
    here, there = LADDER.index(current), LADDER.index(target)
    if there < here:
        if not _manager(ctx.role):
            return False, "only a bid manager may move a pursuit backwards"
        return True, ""
    if there > here + 1:
        return False, f"cannot skip {LADDER[here + 1]}"
    if target == STAGE_DRAFTING and ctx.decision != DECISION_BID:
        return False, GATE_1_REASON
    return True, ""
