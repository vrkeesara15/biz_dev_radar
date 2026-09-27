"""Citation tokens (SPEC 8 grounding). Pure: parsing and formatting only.

Every factual claim an agent writes about the company carries a token that names the
record it came from, so the UI can link it and the grounding validator (M5-11) can check
that it resolves:

    KB:past_performance:0f4e...#2      a chunk of a knowledge-base source (RAG)
    PROFILE:certification:9a11...      a profile record used whole (no chunking)

Tokens appear inside square brackets in draft text ("[KB:past_performance:0f4e...#2]");
`find_tokens` pulls them back out and `parse_token` turns one into a Citation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

KB = "KB"
PROFILE = "PROFILE"
KINDS: tuple[str, ...] = (KB, PROFILE)

# KB:<source_type>:<uuid>#<chunk index>  |  PROFILE:<source_type>:<uuid>
_TOKEN = r"(KB|PROFILE):([a-z_]{2,32}):([0-9a-fA-F-]{36})(?:#(\d{1,4}))?"
TOKEN_RE = re.compile(rf"^{_TOKEN}$")
BRACKETED_RE = re.compile(rf"\[\s*{_TOKEN}\s*\]")


@dataclass(frozen=True, slots=True)
class Citation:
    kind: str  # KB | PROFILE
    source_type: str  # past_performance | boilerplate | profile_file | service_line | ...
    source_id: uuid.UUID
    chunk_index: int | None = None

    @property
    def token(self) -> str:
        suffix = "" if self.chunk_index is None else f"#{self.chunk_index}"
        return f"{self.kind}:{self.source_type}:{self.source_id}{suffix}"

    @property
    def bracketed(self) -> str:
        return f"[{self.token}]"


def kb_token(source_type: str, source_id: uuid.UUID | str, chunk_index: int) -> str:
    return f"{KB}:{source_type}:{source_id}#{int(chunk_index)}"


def profile_token(source_type: str, source_id: uuid.UUID | str) -> str:
    return f"{PROFILE}:{source_type}:{source_id}"


def parse_token(raw: str) -> Citation | None:
    """A Citation, or None when the text is not a well-formed token."""
    match = TOKEN_RE.match((raw or "").strip().strip("[]").strip())
    if match is None:
        return None
    kind, source_type, source_id, index = match.groups()
    try:
        parsed_id = uuid.UUID(source_id)
    except ValueError:  # pragma: no cover - the pattern already constrains the shape
        return None
    return Citation(
        kind=kind,
        source_type=source_type,
        source_id=parsed_id,
        chunk_index=None if index is None else int(index),
    )


def find_tokens(text: str) -> list[str]:
    """Every bracketed token in `text`, in order, without the brackets (duplicates kept)."""
    return [match.group(0).strip("[] ").strip() for match in BRACKETED_RE.finditer(text or "")]


def strip_tokens(text: str) -> str:
    """The text with its bracketed tokens removed (for sentence-level checks)."""
    return re.sub(r"\s{2,}", " ", BRACKETED_RE.sub("", text or "")).strip()
