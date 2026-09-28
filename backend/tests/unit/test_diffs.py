"""M5-17: unified diffs of draft bodies (SPEC 8: human edits are diffed and saved)."""

from __future__ import annotations

from app.core.diffs import (
    MAX_DIFF_CHARS,
    diff_stats,
    example_pair,
    split_for_diff,
    unified_diff,
)

AGENT = (
    "## Technical Approach\n"
    "\n"
    "We will migrate the workloads using our standard runbook.\n"
    "Our team is world-class.\n"
)
EDITED = (
    "## Technical Approach\n"
    "\n"
    "We will migrate 400 workloads using the runbook we used at the Treasury.\n"
)


def test_blank_lines_are_dropped_and_lines_are_trimmed() -> None:
    assert split_for_diff("") == []
    assert split_for_diff("  a  \n\n\n b \n") == ["a", "b"]


def test_a_long_paragraph_is_split_into_sentences() -> None:
    long_line = " ".join(f"Sentence number {i} runs on for a while." for i in range(12))
    units = split_for_diff(long_line)
    assert len(units) == 12
    assert units[0] == "Sentence number 0 runs on for a while."
    # a short line stays whole even with several sentences in it
    assert split_for_diff("One. Two.") == ["One. Two."]


def test_the_diff_shows_what_the_writer_removed_and_added() -> None:
    patch = unified_diff(AGENT, EDITED, before_label="v1 (agent)", after_label="v2 (user)")
    assert patch.startswith("--- v1 (agent)")
    assert "+++ v2 (user)" in patch
    assert "-We will migrate the workloads using our standard runbook." in patch
    assert "-Our team is world-class." in patch
    assert "+We will migrate 400 workloads using the runbook we used at the Treasury." in patch
    # the unchanged heading is context, not a change
    assert " ## Technical Approach" in patch


def test_stats_count_the_changed_units_and_ignore_the_headers() -> None:
    stats = diff_stats(unified_diff(AGENT, EDITED))
    assert stats.added == 1 and stats.removed == 2 and stats.changed
    assert stats.as_dict() == {"added": 1, "removed": 2, "changed": True}


def test_an_edit_that_changes_nothing_produces_an_empty_diff() -> None:
    patch = unified_diff(AGENT, AGENT)
    assert patch == ""
    assert not diff_stats(patch).changed


def test_whitespace_only_reflow_is_not_a_change() -> None:
    """A rich-text editor re-wraps constantly; that is not drafting feedback."""
    reflowed = AGENT.replace("\n", "\n\n").replace("We will", "  We will  ")
    assert not diff_stats(unified_diff(AGENT, reflowed)).changed


def test_a_huge_diff_is_truncated_with_a_marker() -> None:
    before = "\n".join(f"line {i}" for i in range(40_000))
    patch = unified_diff(before, "", max_chars=500)
    assert len(patch) <= 500 + len("\n... (diff truncated)")
    assert patch.endswith("... (diff truncated)")
    assert MAX_DIFF_CHARS > 500


def test_example_pairs_are_capped_for_a_prompt() -> None:
    before, after = example_pair("a" * 9_000, "b" * 10, max_chars=100)
    assert len(before) == 100 and after == "b" * 10
