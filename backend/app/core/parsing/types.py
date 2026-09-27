"""Parsed-document types shared by every parser (SPEC 8 agent 1: pages + sections with
page numbers) and the OCR protocol the PDF parser calls for pages without a text layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# A PDF page with fewer extractable characters than this is treated as scanned.
MIN_TEXT_CHARS = 20
DEFAULT_OCR_LANGUAGES = "eng+hin"


class OCR(Protocol):
    """Image bytes (PNG) -> text. Implementations: services.ocr.TesseractOCR, tests' FakeOCR."""

    def image_to_text(self, png: bytes, *, languages: str = DEFAULT_OCR_LANGUAGES) -> str: ...


@dataclass(slots=True)
class Page:
    number: int  # 1-based
    text: str
    ocr: bool = False  # text came from OCR (no text layer)


@dataclass(slots=True)
class Section:
    title: str
    page_start: int
    page_end: int
    text: str


@dataclass(slots=True)
class ParsedDocument:
    kind: str  # pdf | docx | xlsx
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    sha256: str = ""
    page_count: int = 0
    parser: str = ""  # pymupdf | pdfplumber | python-docx | openpyxl
    ocr_pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\f".join(p.text for p in self.pages)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


class ParseError(ValueError):
    """The bytes could not be parsed by any available parser."""


class UnsupportedDocumentError(ParseError):
    """Not a PDF, DOCX or XLSX."""
