"""Overlapping chunks tagged with their page, ready for embedding (SPEC 10.2 document_chunks).

~800 tokens per chunk approximated as 3,200 characters, 200 characters of overlap. The
document text is walked as one stream (pages joined with a blank line) so a paragraph
that crosses a page break stays together; a chunk is tagged with the page where it
starts.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.parsing.types import Page

CHUNK_CHARS = 3200
CHUNK_OVERLAP = 200
_PAGE_JOIN = "\n\n"


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    page: int | None
    text: str
    start: int  # offset into the joined document text


def _page_at(offsets: list[tuple[int, int]], position: int) -> int | None:
    page: int | None = None
    for start, number in offsets:
        if start <= position:
            page = number
        else:
            break
    return page


def _break_point(text: str, start: int, end: int) -> int:
    """Prefer to end a chunk at a paragraph or sentence boundary in its last quarter."""
    if end >= len(text):
        return len(text)
    floor = start + (end - start) * 3 // 4
    for marker in ("\n\n", "\n", ". ", " "):
        idx = text.rfind(marker, floor, end)
        if idx > start:
            return idx + len(marker)
    return end


def chunk_pages(
    pages: list[Page], *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[Chunk]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and larger than the overlap")
    offsets: list[tuple[int, int]] = []
    parts: list[str] = []
    position = 0
    for page in pages:
        offsets.append((position, page.number))
        parts.append(page.text)
        position += len(page.text) + len(_PAGE_JOIN)
    text = _PAGE_JOIN.join(parts)
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = _break_point(text, start, min(start + size, len(text)))
        body = text[start:end]
        if body.strip():
            first_ink = start + (len(body) - len(body.lstrip()))
            chunks.append(
                Chunk(index=len(chunks), page=_page_at(offsets, first_ink), text=body, start=start)
            )
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
