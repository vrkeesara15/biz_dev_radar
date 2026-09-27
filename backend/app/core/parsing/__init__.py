"""Document parsing (SPEC 8 agent 1, 10.1): PDF (PyMuPDF, pdfplumber fallback, OCR for
scanned pages), DOCX (python-docx), XLSX (openpyxl) -> ParsedDocument with page-numbered
text and sections; `chunk_pages` produces overlapping, page-tagged chunks.

    parsed = parse_document(data, file_name="sow.pdf", ocr=TesseractOCR())
    for section in parsed.sections: ...
    for chunk in chunk_pages(parsed.pages): ...
"""

from __future__ import annotations

from app.core.parsing.chunk import CHUNK_CHARS, CHUNK_OVERLAP, Chunk, chunk_pages
from app.core.parsing.docx import parse_docx
from app.core.parsing.pdf import parse_pdf
from app.core.parsing.types import (
    DEFAULT_OCR_LANGUAGES,
    MIN_TEXT_CHARS,
    OCR,
    Page,
    ParsedDocument,
    ParseError,
    Section,
    UnsupportedDocumentError,
)
from app.core.parsing.xlsx import parse_xlsx

__all__ = [
    "CHUNK_CHARS",
    "CHUNK_OVERLAP",
    "DEFAULT_OCR_LANGUAGES",
    "MIN_TEXT_CHARS",
    "OCR",
    "Chunk",
    "Page",
    "ParseError",
    "ParsedDocument",
    "Section",
    "UnsupportedDocumentError",
    "chunk_pages",
    "detect_kind",
    "parse_document",
    "parse_docx",
    "parse_pdf",
    "parse_xlsx",
]

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_KIND_BY_EXT = {"pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "xlsm": "xlsx"}
_KIND_BY_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


def detect_kind(data: bytes, *, file_name: str | None = None, mime_type: str | None = None) -> str:
    """pdf | docx | xlsx from the bytes first (magic + OOXML directory), then hints."""
    head = data[:1024]
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if head.startswith(_ZIP_MAGIC):
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            names = []
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
    ext = (file_name or "").rsplit(".", 1)[-1].lower() if file_name and "." in file_name else ""
    if ext in _KIND_BY_EXT:
        return _KIND_BY_EXT[ext]
    if mime_type and mime_type.split(";")[0].strip().lower() in _KIND_BY_MIME:
        return _KIND_BY_MIME[mime_type.split(";")[0].strip().lower()]
    raise UnsupportedDocumentError(
        f"unsupported document (name={file_name!r}, mime={mime_type!r}, head={head[:8]!r})"
    )


def parse_document(
    data: bytes,
    *,
    file_name: str | None = None,
    mime_type: str | None = None,
    ocr: OCR | None = None,
    languages: str = DEFAULT_OCR_LANGUAGES,
) -> ParsedDocument:
    kind = detect_kind(data, file_name=file_name, mime_type=mime_type)
    if kind == "pdf":
        return parse_pdf(data, ocr=ocr, languages=languages)
    if kind == "docx":
        return parse_docx(data)
    return parse_xlsx(data)
