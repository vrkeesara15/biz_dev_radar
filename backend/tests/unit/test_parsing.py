"""M2-12: PDF/DOCX/XLSX parsers, OCR fallback, sections and the chunker (pure)."""

from itertools import pairwise
from pathlib import Path

import pytest
from app.core.parsing import (
    Page,
    ParseError,
    UnsupportedDocumentError,
    chunk_pages,
    detect_kind,
    parse_document,
    parse_docx,
    parse_pdf,
    parse_xlsx,
)
from app.core.parsing.sections import detect_sections, is_heading
from app.services.ocr import OCRUnavailableError, TesseractOCR, ocr_from_settings

from tests.ocr_fake import FakeOCR

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"
TEXT_PDF = (FIXTURES / "text.pdf").read_bytes()
SCANNED_PDF = (FIXTURES / "scanned.pdf").read_bytes()
DOCX = (FIXTURES / "sample.docx").read_bytes()
XLSX = (FIXTURES / "sample.xlsx").read_bytes()


def test_text_pdf_pages_sections_and_hash() -> None:
    ocr = FakeOCR()
    parsed = parse_pdf(TEXT_PDF, ocr=ocr)
    assert parsed.kind == "pdf" and parsed.parser == "pymupdf"
    assert parsed.page_count == 3 and [p.number for p in parsed.pages] == [1, 2, 3]
    assert "40 field offices" in parsed.pages[0].text
    assert "FedRAMP Moderate" in parsed.pages[1].text
    assert "20 October 2026" in parsed.pages[2].text
    assert len(parsed.sha256) == 64
    assert ocr.calls == [] and parsed.ocr_pages == 0 and parsed.warnings == []
    titles = [s.title for s in parsed.sections]
    assert titles[:3] == [
        "SECTION 1 INTRODUCTION",
        "SECTION 2 REQUIREMENTS",
        "SECTION 3 EVALUATION",
    ]
    by_title = {s.title: s for s in parsed.sections}
    assert (
        by_title["SECTION 2 REQUIREMENTS"].page_start,
        by_title["SECTION 2 REQUIREMENTS"].page_end,
    ) == (2, 2)
    assert "migration plan within 30 days" in by_title["SECTION 2 REQUIREMENTS"].text
    assert parsed.text.count("\f") == 2


def test_scanned_pdf_uses_ocr_per_page() -> None:
    ocr = FakeOCR(texts=["SECTION 1 INTRODUCTION\nscanned page one", "scanned page two"])
    parsed = parse_pdf(SCANNED_PDF, ocr=ocr, languages="eng+hin")
    assert parsed.page_count == 2 and parsed.ocr_pages == 2
    assert [c[1] for c in ocr.calls] == ["eng+hin", "eng+hin"]
    assert all(size > 1000 for size, _ in ocr.calls)
    assert [p.text for p in parsed.pages] == [
        "SECTION 1 INTRODUCTION\nscanned page one",
        "scanned page two",
    ]
    assert all(p.ocr for p in parsed.pages)
    assert parsed.sections[0].title == "SECTION 1 INTRODUCTION"


def test_scanned_pdf_without_ocr_keeps_empty_pages_and_warns() -> None:
    parsed = parse_pdf(SCANNED_PDF)
    assert parsed.page_count == 2 and parsed.ocr_pages == 0
    assert [p.text for p in parsed.pages] == ["", ""]
    assert parsed.warnings == [
        "page 1: no text layer and no OCR",
        "page 2: no text layer and no OCR",
    ]


def test_pdfplumber_fallback_when_pymupdf_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import pymupdf

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("mupdf exploded")

    monkeypatch.setattr(pymupdf, "open", boom)
    parsed = parse_pdf(TEXT_PDF)
    assert parsed.parser == "pdfplumber" and parsed.page_count == 3
    assert "40 field offices" in parsed.pages[0].text
    assert parsed.warnings == ["pymupdf failed (RuntimeError); used pdfplumber"]


def test_garbage_pdf_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_pdf(b"%PDF-1.7 this is not really a pdf")


def test_docx_headings_table_and_page_break() -> None:
    parsed = parse_docx(DOCX)
    assert parsed.kind == "docx" and parsed.parser == "python-docx"
    assert parsed.page_count == 2
    assert "migration plan within 30 days" in parsed.pages[0].text
    assert "Desktop computer\t120\teach" in parsed.pages[0].text
    assert "Technical 70, commercial 30." in parsed.pages[1].text
    titles = [(s.title, s.page_start, s.page_end) for s in parsed.sections]
    assert titles == [("Statement of Work", 1, 1), ("Eligibility", 1, 2), ("Evaluation", 2, 2)]
    assert "INR 5 crore" in parsed.sections[1].text


def test_xlsx_one_page_per_sheet() -> None:
    parsed = parse_xlsx(XLSX)
    assert parsed.kind == "xlsx" and parsed.page_count == 2
    assert [s.title for s in parsed.sections] == ["Pricing", "Key dates"]
    assert parsed.pages[0].text.splitlines()[0] == "Labor category\tHours\tRate"
    assert "Cloud architect\t400\t185.5" in parsed.pages[0].text
    assert "Migration engineer\t1200\t120" in parsed.pages[0].text
    assert "Proposals due\t2026-10-20" in parsed.pages[1].text


def test_detect_kind_by_magic_then_hints() -> None:
    assert detect_kind(TEXT_PDF) == "pdf"
    assert detect_kind(DOCX) == "docx"
    assert detect_kind(XLSX) == "xlsx"
    assert detect_kind(b"", file_name="x.PDF") == "pdf"
    assert detect_kind(b"", mime_type="application/pdf; charset=binary") == "pdf"
    with pytest.raises(UnsupportedDocumentError):
        detect_kind(b"hello", file_name="notes.txt", mime_type="text/plain")


def test_parse_document_dispatches() -> None:
    assert parse_document(DOCX).kind == "docx"
    assert parse_document(XLSX, file_name="x.bin").kind == "xlsx"
    assert parse_document(TEXT_PDF, ocr=FakeOCR()).kind == "pdf"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("SECTION L INSTRUCTIONS TO OFFERORS", True),
        ("1.2 Background", True),
        ("Annexure A", True),
        ("Eligibility Criteria", True),
        ("The contractor shall migrate all workloads within 18 months of award.", False),
        ("1.1 Purpose. The Internal Revenue Service seeks cloud migration services.", False),
        ("", False),
        ("42", False),
        ("A" * 100, False),
    ],
)
def test_is_heading(line: str, expected: bool) -> None:
    assert is_heading(line) is expected


def test_detect_sections_without_headings_is_one_document_section() -> None:
    pages = [Page(1, "plain text only."), Page(2, "more plain text.")]
    sections = detect_sections(pages)
    assert [(s.title, s.page_start, s.page_end) for s in sections] == [("Document", 1, 2)]
    assert sections[0].text == "plain text only.\nmore plain text."


def test_chunker_windows_overlap_and_page_tags() -> None:
    pages = [Page(n, f"p{n} word " * 700) for n in (1, 2, 3)]  # ~5,600 chars each
    chunks = chunk_pages(pages, size=3200, overlap=200)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 3200 for c in chunks)
    joined = "\n\n".join(p.text for p in pages)
    for a, b in pairwise(chunks):
        assert b.start == a.start + len(a.text) - 200  # exactly 200 chars of overlap
        assert joined[b.start : b.start + len(b.text)] == b.text
    assert chunks[0].page == 1 and chunks[-1].page == 3
    assert {c.page for c in chunks} == {1, 2, 3}
    # every character of the document is covered by some chunk
    covered = set()
    for c in chunks:
        covered.update(range(c.start, c.start + len(c.text)))
    assert covered == set(range(len(joined)))


def test_chunker_prefers_boundaries_and_skips_blank() -> None:
    text = "Para one.\n\n" + ("sentence. " * 400) + "\n\nLast para."
    chunks = chunk_pages([Page(1, text)], size=1000, overlap=100)
    assert len(chunks) >= 4
    assert chunks[0].text.endswith(("\n\n", ". ", " "))
    assert chunk_pages([Page(1, "   \n  ")]) == []
    assert chunk_pages([]) == []
    small = chunk_pages([Page(7, "tiny")])
    assert len(small) == 1 and small[0].page == 7 and small[0].text == "tiny"
    with pytest.raises(ValueError):
        chunk_pages([Page(1, "x")], size=100, overlap=100)


def test_tesseract_wrapper_is_import_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    import pymupdf

    png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False).tobytes("png")
    ocr = TesseractOCR(languages="eng+hin", tesseract_cmd="/nonexistent/tesseract")
    assert ocr.languages == "eng+hin"
    assert ocr.available() is False
    with pytest.raises(OCRUnavailableError):
        ocr.image_to_text(png)


def test_ocr_from_settings() -> None:
    from app.core.config import Settings

    assert ocr_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    ocr = ocr_from_settings(Settings(_env_file=None, ocr_backend="tesseract", ocr_languages="eng"))  # type: ignore[call-arg]
    assert isinstance(ocr, TesseractOCR) and ocr.languages == "eng"
