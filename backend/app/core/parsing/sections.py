"""Heading detection over page text -> sections with page ranges (SPEC 8 agent 1)."""

from __future__ import annotations

import re

from app.core.parsing.types import Page, Section

MAX_HEADING_CHARS = 90
DEFAULT_TITLE = "Document"

# "1.", "1.2.3", "SECTION L", "PART II", "ANNEXURE A", "Volume 2", "Appendix B" ...
_NUMBERED_RE = re.compile(r"^\s*(\d+(\.\d+)*\.?|[A-Z]\.|\([a-z0-9]+\))\s+\S")
_KEYWORD_RE = re.compile(
    r"^\s*(section|part|chapter|annex(ure)?|appendix|schedule|volume|attachment|exhibit|"
    r"article|clause|scope|introduction|background|eligibility|evaluation|instructions|"
    r"terms|conditions|technical|financial|price|bid|tender|general)\b",
    re.I,
)


def is_heading(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > MAX_HEADING_CHARS or text.endswith((".", ",", ";")):
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    if _NUMBERED_RE.match(text) and len(text.split()) <= 12:
        return True
    if text.upper() == text and len(text.split()) <= 10:
        return True
    return bool(_KEYWORD_RE.match(text)) and len(text.split()) <= 8 and text[0].isupper()


def detect_sections(pages: list[Page]) -> list[Section]:
    """Split pages into sections at heading lines; text before the first heading (or a
    document without headings) becomes one section titled 'Document'."""
    sections: list[Section] = []
    title = DEFAULT_TITLE
    start = pages[0].number if pages else 1
    buffer: list[str] = []
    current_page = start
    last_body_page = start  # last page that contributed text to the open section

    def close() -> None:
        body = "\n".join(buffer).strip()
        if body or (sections and title != DEFAULT_TITLE):
            end = max(start, last_body_page)
            sections.append(Section(title=title, page_start=start, page_end=end, text=body))

    for page in pages:
        current_page = page.number
        for line in page.text.splitlines():
            if is_heading(line):
                close()
                title, start, buffer = line.strip(), page.number, []
                last_body_page = page.number
                continue
            buffer.append(line)
            if line.strip():
                last_body_page = page.number
    close()
    if not sections and pages:
        sections.append(
            Section(title=DEFAULT_TITLE, page_start=start, page_end=current_page, text="")
        )
    return sections
