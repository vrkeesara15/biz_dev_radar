"""Regenerate the document fixtures (M2-12).

Run: uv run python tests/adapters/fixtures/documents/make_fixtures.py

text.pdf     3 pages with a text layer (headings, numbered clauses, a Hindi line)
scanned.pdf  2 image-only pages (rendered from text, no text layer) -> OCR path
sample.docx  headings, paragraphs, a table and one explicit page break
sample.xlsx  two sheets (pricing rows, key dates)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import docx
import openpyxl
import pymupdf

HERE = Path(__file__).resolve().parent

PAGES = [
    (
        "SECTION 1 INTRODUCTION",
        [
            "1.1 Purpose. The Internal Revenue Service seeks cloud migration services for "
            "40 field offices.",
            "1.2 Background. Legacy workloads run on-premises in Kansas City and Ogden.",
            "The contractor shall migrate all workloads within 18 months of award.",
        ],
    ),
    (
        "SECTION 2 REQUIREMENTS",
        [
            "2.1 The contractor shall provide a migration plan within 30 days.",
            "2.2 The contractor must hold FedRAMP Moderate authorization.",
            "2.3 Offerors should describe their approach to data residency.",
            "निविदा दस्तावेज़ का यह भाग हिंदी में है।",
        ],
    ),
    (
        "SECTION 3 EVALUATION",
        [
            "3.1 Technical approach (40 points).",
            "3.2 Past performance (30 points).",
            "3.3 Price (30 points). Proposals are due 20 October 2026 at 2:00 PM Eastern.",
        ],
    ),
]


def text_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for heading, lines in PAGES:
        page = doc.new_page()
        y = 72
        page.insert_text((72, y), heading, fontsize=14)
        for line in lines:
            y += 24
            font = "helv"
            if any("ऀ" <= ch <= "ॿ" for ch in line):
                # Devanagari needs a Unicode font; fall back to a transliteration marker
                line = "[Hindi text: nivida dastavez ka yah bhaag Hindi mein hai]"
            page.insert_text((72, y), line, fontsize=10, fontname=font)
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def scanned_pdf(path: Path) -> None:
    """Render two text pages to images and build a PDF that contains only the images."""
    source = pymupdf.open()
    for heading, lines in PAGES[:2]:
        page = source.new_page()
        page.insert_text((72, 72), heading, fontsize=14)
        y = 72
        for line in lines[:2]:
            y += 24
            page.insert_text((72, y), line, fontsize=10)
    out = pymupdf.open()
    for page in source:
        pix = page.get_pixmap(dpi=72)
        img = out.new_page(width=page.rect.width, height=page.rect.height)
        img.insert_image(img.rect, stream=pix.tobytes("png"))
    out.save(path, garbage=4, deflate=True)
    out.close()
    source.close()


def sample_docx(path: Path) -> None:
    d = docx.Document()
    d.add_heading("Statement of Work", level=1)
    d.add_paragraph("The contractor shall deliver a migration plan within 30 days of award.")
    d.add_heading("Eligibility", level=2)
    d.add_paragraph("Bidders must have an average annual turnover of INR 5 crore.")
    table = d.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "Item"
    table.rows[0].cells[1].text = "Quantity"
    table.rows[0].cells[2].text = "Unit"
    table.rows[1].cells[0].text = "Desktop computer"
    table.rows[1].cells[1].text = "120"
    table.rows[1].cells[2].text = "each"
    d.add_page_break()
    d.add_heading("Evaluation", level=2)
    d.add_paragraph("Technical 70, commercial 30.")
    d.save(path)


def sample_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pricing"
    ws.append(["Labor category", "Hours", "Rate"])
    ws.append(["Cloud architect", 400, 185.5])
    ws.append(["Migration engineer", 1200, 120])
    ws2 = wb.create_sheet("Key dates")
    ws2.append(["Milestone", "Date"])
    ws2.append(["Questions due", date(2026, 10, 5)])
    ws2.append(["Proposals due", date(2026, 10, 20)])
    wb.save(path)


if __name__ == "__main__":
    text_pdf(HERE / "text.pdf")
    scanned_pdf(HERE / "scanned.pdf")
    sample_docx(HERE / "sample.docx")
    sample_xlsx(HERE / "sample.xlsx")
    print("fixtures written to", HERE)
