"""DOCX -> one logical page per explicit page break (or the whole body), sections from
Heading styles (falls back to the shared heading heuristic)."""

from __future__ import annotations

import hashlib
import io
from typing import Any

from app.core.parsing.sections import DEFAULT_TITLE, detect_sections
from app.core.parsing.types import Page, ParsedDocument, ParseError, Section

_PAGE_BREAK = "w:br"


def _has_page_break(paragraph: Any) -> bool:
    for run in paragraph.runs:
        for br in run._element.iter():
            if (
                br.tag.endswith("}br")
                and br.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type")
                == "page"
            ):
                return True
    return False


def _table_text(table: Any) -> str:
    rows = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        rows.append("\t".join(cells))
    return "\n".join(rows)


def parse_docx(data: bytes) -> ParsedDocument:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = ParsedDocument(kind="docx", sha256=hashlib.sha256(data).hexdigest(), parser="python-docx")
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"python-docx: {exc}") from exc

    pages: list[Page] = [Page(number=1, text="")]
    headings: list[tuple[str, int]] = []  # (title, page number)
    lines: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            if text:
                if style.lower().startswith(("heading", "title")):
                    headings.append((text, len(pages)))
                lines.append(text)
            if _has_page_break(paragraph):
                pages[-1].text = "\n".join(lines).strip()
                pages.append(Page(number=len(pages) + 1, text=""))
                lines = []
        elif tag == "tbl":
            lines.append(_table_text(Table(child, document)))
    pages[-1].text = "\n".join(lines).strip()
    doc.pages = pages
    doc.page_count = len(pages)
    doc.sections = _sections_from_headings(pages, headings) or detect_sections(pages)
    return doc


def _sections_from_headings(pages: list[Page], headings: list[tuple[str, int]]) -> list[Section]:
    if not headings:
        return []
    full = "\n".join(p.text for p in pages)
    sections: list[Section] = []
    titles = [h[0] for h in headings]
    # split the concatenated text on the heading lines, in order
    cursor = 0
    positions: list[int] = []
    for title in titles:
        idx = full.find(title, cursor)
        if idx < 0:
            idx = cursor
        positions.append(idx)
        cursor = idx + len(title)
    preamble = full[: positions[0]].strip()
    if preamble:
        sections.append(Section(DEFAULT_TITLE, 1, headings[0][1], preamble))
    for i, (title, page_no) in enumerate(headings):
        start = positions[i] + len(title)
        end = positions[i + 1] if i + 1 < len(positions) else len(full)
        page_end = headings[i + 1][1] if i + 1 < len(headings) else pages[-1].number
        sections.append(Section(title, page_no, page_end, full[start:end].strip()))
    return sections
