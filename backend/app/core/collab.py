"""Tasks and comments vocabulary, and the [NEEDS INPUT] reader (SPEC 8, 9). Pure, no I/O.

    find_placeholders("Rate for [NEEDS INPUT: labor category] and [NEEDS INPUT: price]")
        -> ["labor category", "price"]
    task_title("labor category")  -> "Provide: labor category"

Agents leave `[NEEDS INPUT: <what>]` wherever they refuse to invent a fact (SPEC 8: the
pricing sheet and the drafts never guess). Every distinct placeholder becomes one task so
a human is asked exactly once, however many times the text repeats it.
"""

from __future__ import annotations

import re

TARGET_PURSUIT = "pursuit"
TARGET_REQUIREMENT = "requirement"
TARGET_COMPLIANCE_ITEM = "compliance_item"
TARGET_DRAFT_SECTION = "draft_section"
TARGET_TASK = "task"
TARGET_KEY_DATE = "key_date"
TARGET_ARTIFACT = "artifact"

# What a comment can hang off. `pursuit` is the activity feed; the rest are anchors the
# workspace tabs already address by id.
TARGET_TYPES: tuple[str, ...] = (
    TARGET_PURSUIT,
    TARGET_REQUIREMENT,
    TARGET_COMPLIANCE_ITEM,
    TARGET_DRAFT_SECTION,
    TARGET_TASK,
    TARGET_KEY_DATE,
    TARGET_ARTIFACT,
)

TASK_OPEN = "open"
TASK_DONE = "done"
TASK_STATUSES: tuple[str, ...] = (TASK_OPEN, TASK_DONE)

SOURCE_AGENT = "agent"
SOURCE_USER = "user"
TASK_SOURCES: tuple[str, ...] = (SOURCE_AGENT, SOURCE_USER)

# "[NEEDS INPUT: labor category]" — case-insensitive, tolerant of extra spaces, and it
# never spans a line so a stray bracket cannot swallow a paragraph.
PLACEHOLDER_RE = re.compile(r"\[\s*NEEDS\s+INPUT\s*:\s*([^\]\n]{1,200})\s*\]", re.IGNORECASE)
BARE_PLACEHOLDER_RE = re.compile(r"\[\s*NEEDS\s+INPUT\s*\]", re.IGNORECASE)
BARE_PLACEHOLDER_LABEL = "unspecified input"

MAX_TITLE = 300


def find_placeholders(text: str | None) -> list[str]:
    """Distinct `[NEEDS INPUT: ...]` labels in document order, whitespace collapsed."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for match in PLACEHOLDER_RE.finditer(text):
        label = " ".join(match.group(1).split())
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            out.append(label)
    if BARE_PLACEHOLDER_RE.search(text) and BARE_PLACEHOLDER_LABEL not in seen:
        out.append(BARE_PLACEHOLDER_LABEL)
    return out


def task_title(placeholder: str) -> str:
    """The one-line ask a human sees in their task list."""
    label = " ".join(placeholder.split()) or BARE_PLACEHOLDER_LABEL
    return f"Provide: {label}"[:MAX_TITLE]


def placeholder_key(placeholder: str) -> str:
    """Stable identity for one ask, so re-running an agent never duplicates the task."""
    return " ".join(placeholder.split()).casefold()
