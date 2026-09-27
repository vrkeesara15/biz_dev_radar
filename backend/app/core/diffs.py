"""Unified diffs of draft bodies (SPEC 8: "human-in-the-loop edits are diffed and saved
as feedback to improve future drafts"). Pure: no I/O, no model call.

    patch = unified_diff(agent_text, edited_text, before_label="v1 (agent)", after_label="v2 (you)")
    stats = diff_stats(patch)        # DiffStats(added=3, removed=1, changed=True)

The diff is taken over `body_text` (the rendered plain text), not the HTML: a writer's
editor reflows markup constantly, and what a future prompt wants to learn from is the
wording a human preferred, not a changed `<p>` boundary.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

MAX_DIFF_CHARS = 200_000
CONTEXT_LINES = 3

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_for_diff(text: str) -> list[str]:
    """Diff units: one per non-empty line, long paragraphs split into sentences.

    A proposal paragraph is often one very long line; diffing whole paragraphs would
    record "the paragraph changed" and teach a later prompt nothing. Runs of whitespace
    are collapsed so a rich-text editor re-wrapping the text is not recorded as an edit.
    """
    units: list[str] = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        parts = [p.strip() for p in _SENTENCE_SPLIT.split(line)] if len(line) > 200 else [line]
        units.extend(p for p in parts if p)
    return units


def unified_diff(
    before: str,
    after: str,
    *,
    before_label: str = "before",
    after_label: str = "after",
    context: int = CONTEXT_LINES,
    max_chars: int = MAX_DIFF_CHARS,
) -> str:
    """A unified diff of the two bodies, truncated to `max_chars` with a marker."""
    lines = difflib.unified_diff(
        split_for_diff(before),
        split_for_diff(after),
        fromfile=before_label,
        tofile=after_label,
        lineterm="",
        n=context,
    )
    patch = "\n".join(lines)
    if len(patch) > max_chars:
        patch = patch[:max_chars] + "\n... (diff truncated)"
    return patch


@dataclass(frozen=True, slots=True)
class DiffStats:
    added: int = 0
    removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    def as_dict(self) -> dict[str, int | bool]:
        return {"added": self.added, "removed": self.removed, "changed": self.changed}


def diff_stats(patch: str) -> DiffStats:
    """Count added / removed units in a unified diff (headers and hunks excluded)."""
    added = removed = 0
    for line in (patch or "").splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return DiffStats(added=added, removed=removed)


def example_pair(before: str, after: str, *, max_chars: int = 4_000) -> tuple[str, str]:
    """A (before, after) pair short enough to paste into a future prompt as an example."""
    return (before or "")[:max_chars], (after or "")[:max_chars]
