"""PDF -> pages/sections. PyMuPDF first, pdfplumber as the fallback; pages with (almost)
no text layer are rendered to PNG and handed to the OCR implementation when one is given."""

from __future__ import annotations

import hashlib
import io
from typing import Any

from app.core.parsing.sections import detect_sections
from app.core.parsing.types import (
    DEFAULT_OCR_LANGUAGES,
    MIN_TEXT_CHARS,
    OCR,
    Page,
    ParsedDocument,
    ParseError,
)

OCR_DPI = 200


def _clean(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def _needs_ocr(text: str, min_chars: int) -> bool:
    return len("".join(text.split())) < min_chars


def _with_pymupdf(data: bytes) -> tuple[list[Page], list[bytes | None]]:
    """(pages, renderers) where renderers[i] is the PNG of page i when it needs OCR."""
    import pymupdf

    pages: list[Page] = []
    renders: list[bytes | None] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        if doc.needs_pass:
            raise ParseError("PDF is password protected")
        for index, page in enumerate(doc, start=1):
            text = _clean(page.get_text("text"))
            pages.append(Page(number=index, text=text))
            renders.append(None)
            if _needs_ocr(text, MIN_TEXT_CHARS):
                pix = page.get_pixmap(dpi=OCR_DPI)
                renders[-1] = pix.tobytes("png")
    return pages, renders


def _with_pdfplumber(data: bytes) -> tuple[list[Page], list[bytes | None]]:
    import pdfplumber

    pages: list[Page] = []
    renders: list[bytes | None] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = _clean(page.extract_text() or "")
            pages.append(Page(number=index, text=text))
            png: bytes | None = None
            if _needs_ocr(text, MIN_TEXT_CHARS):
                try:
                    image: Any = page.to_image(resolution=OCR_DPI)
                    buf = io.BytesIO()
                    image.original.save(buf, format="PNG")
                    png = buf.getvalue()
                except Exception:  # rendering is best effort; OCR is then skipped
                    png = None
            renders.append(png)
    return pages, renders


def parse_pdf(
    data: bytes,
    *,
    ocr: OCR | None = None,
    languages: str = DEFAULT_OCR_LANGUAGES,
    min_text_chars: int = MIN_TEXT_CHARS,
) -> ParsedDocument:
    doc = ParsedDocument(kind="pdf", sha256=hashlib.sha256(data).hexdigest())
    try:
        pages, renders = _with_pymupdf(data)
        doc.parser = "pymupdf"
    except ParseError:
        raise
    except Exception as primary:
        try:
            pages, renders = _with_pdfplumber(data)
        except Exception as fallback:
            raise ParseError(f"pymupdf: {primary}; pdfplumber: {fallback}") from fallback
        doc.parser = "pdfplumber"
        doc.warnings.append(f"pymupdf failed ({type(primary).__name__}); used pdfplumber")

    for page, png in zip(pages, renders, strict=True):
        if not _needs_ocr(page.text, min_text_chars):
            continue
        if ocr is None or png is None:
            doc.warnings.append(f"page {page.number}: no text layer and no OCR")
            continue
        page.text = _clean(ocr.image_to_text(png, languages=languages))
        page.ocr = True
        doc.ocr_pages += 1
    doc.pages = pages
    doc.page_count = len(pages)
    doc.sections = detect_sections(pages)
    return doc
