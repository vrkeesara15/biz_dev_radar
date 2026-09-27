"""Regenerate the Devanagari document fixture (M3-07).

Run: uv run python tests/adapters/fixtures/documents/make_hindi_fixture.py

    hindi_tender.pdf   3 pages of a bilingual (Hindi + English) tender notice

Why this is written by hand instead of with PyMuPDF like the other fixtures: drawing
Devanagari needs a Devanagari font, and embedding a system font (Apple's DevanagariMT,
say) in a committed fixture is a licensing question we do not need to answer. A PDF does
not have to embed a font for its text to be extractable: the page's font carries a
`ToUnicode` CMap that maps the single-byte character codes to Unicode code points, which
is exactly how MuPDF, pdfplumber and Tesseract-free text extraction read a page. The
glyphs would render as Helvetica notdefs; the *text layer* - the only thing the parsing,
hashing and chunking path uses - is real Devanagari.

Replace it with a real Hindi tender PDF at the first live capture from an Indian portal
(PROGRESS.m3.md OQ-63).
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "hindi_tender.pdf"
VIRAMA = "\u094d"
_COMBINING = frozenset({"Mn", "Mc"})

PAGES: list[list[str]] = [
    [
        "निविदा सूचना संख्या 12/2026-27",
        "लोक निर्माण विभाग, उत्तर प्रदेश सरकार",
        "Public Works Department, Government of Uttar Pradesh",
        "1. कार्य का नाम: वार्ड संख्या 12 में सड़क निर्माण एवं मरम्मत कार्य।",
        "2. अनुमानित लागत: रु. 45,00,000 (पैंतालीस लाख रुपये मात्र)।",
        "3. धरोहर राशि (EMD): रु. 90,000 राष्ट्रीयकृत बैंक की बैंक गारंटी के रूप में।",
        "4. निविदा शुल्क: रु. 5,000 अप्रतिदेय।",
        "5. निविदा प्रपत्र डाउनलोड करने की अंतिम तिथि: 14-10-2026 अपराह्न 03:00 बजे।",
        "6. निविदा खोलने की तिथि: 15-10-2026 अपराह्न 04:00 बजे।",
        "7. The tender document is available on the e-procurement portal only.",
        "8. निविदादाता के पास वैध जीएसटी पंजीयन एवं पैन होना अनिवार्य है।",
        "9. अपूर्ण अथवा सशर्त निविदाएँ निरस्त कर दी जाएँगी।",
        "10. विभाग को बिना कारण बताए किसी भी निविदा को निरस्त करने का अधिकार है।",
    ],
    [
        "भाग 2: पात्रता शर्तें / PART 2: ELIGIBILITY CONDITIONS",
        "2.1 निविदादाता का विगत तीन वित्तीय वर्षों का औसत वार्षिक कारोबार "
        "रु. 1.35 करोड़ से कम नहीं होना चाहिए।",
        "2.2 The bidder must have at least 3 years of experience in similar road works.",
        "2.3 सूक्ष्म एवं लघु उद्यम (उद्यम पंजीकृत) को धरोहर राशि से छूट अनुमन्य है।",
        "2.4 डीपीआईआईटी मान्यता प्राप्त स्टार्टअप को कारोबार एवं अनुभव में छूट अनुमन्य है।",
        "2.5 निविदादाता के पास वैध डिजिटल हस्ताक्षर प्रमाणपत्र (DSC) होना आवश्यक है।",
        "2.6 कार्य पूर्ण करने की अवधि: कार्यादेश की तिथि से 180 दिन।",
        "2.7 भुगतान माप पुस्तिका में दर्ज माप के आधार पर चालू बिलों के रूप में किया जाएगा।",
        "2.8 Liquidated damages at 0.5% per week, subject to a maximum of 10% of the "
        "contract value, shall apply for delay attributable to the contractor.",
        "2.9 संविदा के दौरान श्रमिकों को न्यूनतम मजदूरी अधिनियम के अनुसार भुगतान अनिवार्य है।",
        "2.10 सभी विवाद अधीक्षण अभियंता के समक्ष प्रस्तुत किए जाएँगे जिनका निर्णय अंतिम होगा।",
    ],
    [
        "भाग 3: अनुसूची / PART 3: SCHEDULE OF QUANTITIES",
        "क्र.सं. | मद का विवरण | इकाई | मात्रा",
        "1 | मिट्टी की खुदाई एवं समतलीकरण | घन मीटर | 1200",
        "2 | जल बंधक परत (WBM) | वर्ग मीटर | 8400",
        "3 | बिटुमिनस कंक्रीट 40 मिमी | वर्ग मीटर | 8400",
        "4 | नाली निर्माण आरसीसी | मीटर | 640",
        "5 | सड़क चिह्नांकन एवं संकेतक | नग | 48",
        "Note: Quantities are indicative; payment is on actual measurement.",
        "संपर्क: अधिशासी अभियंता, निर्माण खंड-2, लखनऊ। दूरभाष: 0522-000000।",
        "यह सूचना विभागीय सूचना पट्ट एवं ई-निविदा पोर्टल पर भी प्रदर्शित है।",
        "अस्वीकरण: निविदा की शर्तें पोर्टल पर उपलब्ध मूल दस्तावेज़ से सत्यापित करें।",
    ],
]


def _clusters(text: str) -> list[str]:
    """Devanagari grapheme clusters: a base letter plus its matras, and consonants joined
    by a virama. Real Indic PDFs put one glyph (one CID) per cluster; keeping the same
    granularity is what makes the extracted text come back unbroken."""
    out: list[str] = []
    current = ""
    for char in text:
        if not current:
            current = char
            continue
        if unicodedata.category(char) in _COMBINING or current.endswith(VIRAMA):
            current += char
        else:
            out.append(current)
            current = char
    if current:
        out.append(current)
    return out


def _cids(pages: list[list[str]]) -> dict[str, int]:
    """One CID per distinct cluster (Identity-H: two bytes per code, so no 255 limit)."""
    table: dict[str, int] = {}
    for lines in pages:
        for line in lines:
            for cluster in _clusters(line):
                if cluster not in table:
                    table[cluster] = len(table) + 1
    return table


def _hex(text: str, cids: dict[str, int]) -> str:
    return "".join(f"{cids[cluster]:04X}" for cluster in _clusters(text))


def _to_unicode(cids: dict[str, int]) -> str:
    pairs = "".join(
        f"<{cid:04X}> <{''.join(f'{ord(c):04X}' for c in cluster)}>\n"
        for cluster, cid in cids.items()
    )
    return (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CMapName /BidRadar-Hindi def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{len(cids)} beginbfchar\n{pairs}endbfchar\nendcmap\n"
        "CMapName currentdict /CMap defineresource pop\nend\nend"
    )


def _content(lines: list[str], cids: dict[str, int]) -> str:
    parts = ["BT", "/F1 11 Tf", "16 TL", "56 780 Td"]
    parts += [f"<{_hex(line, cids)}> Tj T*" for line in lines]
    parts.append("ET")
    return "\n".join(parts)


def build_pdf(pages: list[list[str]]) -> bytes:
    cids = _cids(pages)
    count = len(pages)
    first_page = 3
    first_content = first_page + count
    font = first_content + count
    descendant, descriptor, cmap_obj = font + 1, font + 2, font + 3
    kids = " ".join(f"{first_page + i} 0 R" for i in range(count))

    objects: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>",
    ]
    for index in range(count):
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> "
            f"/Contents {first_content + index} 0 R >>"
        )
    for lines in pages:
        stream = _content(lines, cids)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append(
        "<< /Type /Font /Subtype /Type0 /BaseFont /BidRadarHindi /Encoding /Identity-H "
        f"/DescendantFonts [{descendant} 0 R] /ToUnicode {cmap_obj} 0 R >>"
    )
    objects.append(
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /BidRadarHindi "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor {descriptor} 0 R /DW 620 >>"
    )
    objects.append(
        "<< /Type /FontDescriptor /FontName /BidRadarHindi /Flags 4 "
        "/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 "
        "/CapHeight 700 /StemV 80 >>"
    )
    cmap = _to_unicode(cids)
    objects.append(f"<< /Length {len(cmap)} >>\nstream\n{cmap}\nendstream")

    out = bytearray(b"%PDF-1.4\n%\xe0\xe1\xe2\xe3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


if __name__ == "__main__":
    OUT.write_bytes(build_pdf(PAGES))
    print("wrote", OUT, OUT.stat().st_size, "bytes")
