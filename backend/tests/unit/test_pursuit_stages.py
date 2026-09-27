"""Stage rules (SPEC 9, M6-01): the pure table the board and PATCH /pursuits drag against."""

from __future__ import annotations

from itertools import pairwise

import pytest
from app.core.pursuit_stages import (
    DECISION_BID,
    DECISION_NO_BID,
    GATE_1_REASON,
    LADDER,
    OPEN_STAGES,
    STAGES,
    TERMINAL_STAGES,
    TransitionContext,
    can_transition,
    is_open,
    is_stage,
    is_terminal,
    next_stage,
)
from app.core.roles import Role

MANAGER = TransitionContext(role=Role.BID_MANAGER)
WRITER = TransitionContext(role=Role.WRITER)
BID = TransitionContext(decision=DECISION_BID)
BID_WRITER = TransitionContext(decision=DECISION_BID, role=Role.WRITER)


def test_stage_inventory_matches_spec_9() -> None:
    assert STAGES == (
        "identified",
        "qualifying",
        "bid_decision",
        "drafting",
        "in_review",
        "final_approval",
        "submitted",
        "awarded",
        "lost",
        "cancelled",
        "no_bid",
    )
    assert LADDER[-1] == "submitted"
    assert TERMINAL_STAGES == ("awarded", "lost", "cancelled", "no_bid")
    # reminders keep firing until submitted / passed / cancelled
    assert LADDER[:-1] == OPEN_STAGES
    assert all(is_stage(s) for s in STAGES)
    assert not is_stage("Drafting")


def test_terminal_and_open_predicates() -> None:
    assert is_terminal("awarded") and is_terminal("no_bid")
    assert not is_terminal("submitted")
    assert is_open("drafting")
    assert not is_open("submitted") and not is_open("cancelled")


def test_next_stage_walks_the_ladder() -> None:
    assert next_stage("identified") == "qualifying"
    assert next_stage("final_approval") == "submitted"
    assert next_stage("submitted") is None
    assert next_stage("awarded") is None


def test_every_forward_step_of_the_ladder_is_allowed() -> None:
    for current, target in pairwise(LADDER):
        ok, reason = can_transition(current, target, BID)
        assert ok, f"{current} -> {target}: {reason}"
        assert reason == ""


def test_a_no_op_move_is_always_allowed() -> None:
    for stage in STAGES:
        assert can_transition(stage, stage, WRITER) == (True, "")


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ("identified", "bid_decision", "cannot skip qualifying"),
        ("identified", "drafting", "cannot skip qualifying"),
        ("qualifying", "in_review", "cannot skip bid_decision"),
        ("bid_decision", "final_approval", "cannot skip drafting"),
        ("drafting", "submitted", "cannot skip in_review"),
    ],
)
def test_skipping_a_stage_names_the_missing_one(current: str, target: str, expected: str) -> None:
    ok, reason = can_transition(current, target, BID)
    assert (ok, reason) == (False, expected)


def test_gate_1_blocks_drafting_without_a_bid_decision() -> None:
    assert can_transition("bid_decision", "drafting", TransitionContext()) == (False, GATE_1_REASON)
    assert can_transition(
        "bid_decision", "drafting", TransitionContext(decision=DECISION_NO_BID)
    ) == (False, GATE_1_REASON)
    assert can_transition("bid_decision", "drafting", BID) == (True, "")


def test_gate_1_also_guards_a_backwards_move_into_drafting() -> None:
    # a manager may drag in_review back to drafting, but only when Gate 1 said "bid"
    assert can_transition("in_review", "drafting", MANAGER) == (True, "")
    assert can_transition("cancelled", "drafting", MANAGER) == (False, GATE_1_REASON)
    assert can_transition(
        "cancelled", "drafting", TransitionContext(decision=DECISION_BID, role=Role.BID_MANAGER)
    ) == (True, "")


@pytest.mark.parametrize("target", ["awarded", "lost"])
@pytest.mark.parametrize("current", ["identified", "drafting", "in_review", "final_approval"])
def test_an_outcome_needs_a_submitted_pursuit(current: str, target: str) -> None:
    ok, reason = can_transition(current, target, BID)
    assert (ok, reason) == (False, f"only a submitted pursuit can be marked {target}")


@pytest.mark.parametrize("target", ["awarded", "lost"])
def test_submitted_can_be_won_or_lost(target: str) -> None:
    assert can_transition("submitted", target, BID) == (True, "")


@pytest.mark.parametrize("current", ["qualifying", "bid_decision"])
def test_no_bid_from_the_decision_stages(current: str) -> None:
    assert can_transition(current, "no_bid", MANAGER) == (True, "")


@pytest.mark.parametrize("current", ["identified", "drafting", "in_review", "submitted"])
def test_no_bid_is_refused_once_the_work_started(current: str) -> None:
    ok, reason = can_transition(current, "no_bid", MANAGER)
    assert not ok
    assert "only available from qualifying or bid_decision" in reason


@pytest.mark.parametrize("current", [*LADDER])
def test_any_live_stage_can_be_cancelled(current: str) -> None:
    assert can_transition(current, "cancelled", BID_WRITER) == (True, "")


@pytest.mark.parametrize("current", [*TERMINAL_STAGES])
def test_a_finished_pursuit_cannot_be_cancelled_again(current: str) -> None:
    ok, reason = can_transition(current, "cancelled", MANAGER)
    if current == "cancelled":
        assert (ok, reason) == (True, "")  # no-op
    else:
        assert (ok, reason) == (False, f"a {current} pursuit cannot be cancelled")


def test_backwards_moves_are_manager_only() -> None:
    assert can_transition("in_review", "qualifying", MANAGER) == (True, "")
    assert can_transition("in_review", "qualifying", TransitionContext(role=Role.TENANT_OWNER)) == (
        True,
        "",
    )
    ok, reason = can_transition("in_review", "qualifying", WRITER)
    assert (ok, reason) == (False, "only a bid manager may move a pursuit backwards")
    for role in (Role.REVIEWER, Role.VIEWER):
        assert can_transition("in_review", "qualifying", TransitionContext(role=role))[0] is False


def test_reopening_a_terminal_pursuit_is_manager_only() -> None:
    assert can_transition("no_bid", "qualifying", MANAGER) == (True, "")
    assert can_transition("lost", "in_review", MANAGER) == (True, "")
    ok, reason = can_transition("no_bid", "qualifying", WRITER)
    assert (ok, reason) == (False, "only a bid manager may reopen a no_bid pursuit")


def test_unknown_stages_are_refused_not_raised() -> None:
    ok, reason = can_transition("identified", "Drafting", MANAGER)
    assert not ok
    assert reason.startswith("unknown stage 'Drafting'")
    assert "identified, qualifying" in reason
    assert can_transition("shortlisted", "drafting", MANAGER) == (
        False,
        "unknown stage 'shortlisted'",
    )


def test_a_writer_may_still_move_a_pursuit_forward() -> None:
    assert can_transition("identified", "qualifying", WRITER) == (True, "")
    assert can_transition("bid_decision", "drafting", BID_WRITER) == (True, "")


def test_reason_is_empty_exactly_when_allowed() -> None:
    for current in STAGES:
        for target in STAGES:
            for ctx in (MANAGER, WRITER, BID, BID_WRITER):
                ok, reason = can_transition(current, target, ctx)
                assert ok is (reason == "")
