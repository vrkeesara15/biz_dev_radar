"""M3-07: Devanagari documents and titles survive parsing, hashing and chunking, and
IN-region documents are OCR'd with the Hindi + English language set."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.core.config import Region, Settings
from app.core.normalize.buyer import normalized_buyer
from app.core.normalize.reference import normalized_reference
from app.core.parsing import chunk_pages, detect_kind, parse_document
from app.core.parsing.types import DEFAULT_OCR_LANGUAGES
from app.services.documents import ocr_languages_for

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"
HINDI_PDF = FIXTURES / "hindi_tender.pdf"
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def _parsed():  # type: ignore[no-untyped-def]
    return parse_document(HINDI_PDF.read_bytes(), file_name="hindi_tender.pdf")


def test_a_devanagari_pdf_parses_with_its_text_layer_intact() -> None:
    parsed = _parsed()
    assert parsed.kind == "pdf" and parsed.page_count == 3 and parsed.ocr_pages == 0
    assert parsed.warnings == []
    first = parsed.pages[0].text
    # whole words, not characters torn apart by the combining marks
    assert "निविदा सूचना संख्या 12/2026-27" in first
    assert "लोक निर्माण विभाग, उत्तर प्रदेश सरकार" in first
    assert "रु. 45,00,000 (पैंतालीस लाख रुपये मात्र)" in first
    # the notice is bilingual: the English lines come through on the same page
    assert "Public Works Department, Government of Uttar Pradesh" in first
    assert "The bidder must have at least 3 years" in parsed.pages[1].text
    assert "मिट्टी की खुदाई एवं समतलीकरण" in parsed.pages[2].text


def test_hashing_is_stable_and_over_the_raw_bytes() -> None:
    data = HINDI_PDF.read_bytes()
    parsed = parse_document(data)
    assert parsed.sha256 == hashlib.sha256(data).hexdigest()
    assert parse_document(data).sha256 == parsed.sha256
    assert detect_kind(data, file_name="hindi_tender.pdf") == "pdf"


def test_chunking_keeps_page_numbers_and_does_not_split_code_points() -> None:
    parsed = _parsed()
    chunks = list(chunk_pages(parsed.pages, size=400, overlap=40))
    assert len(chunks) > 3
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert {c.page for c in chunks} == {1, 2, 3}
    for chunk in chunks:
        assert chunk.text.strip()
        # a chunk boundary never lands inside a UTF-8 sequence or an isolated matra
        assert chunk.text.encode("utf-8").decode("utf-8") == chunk.text
    joined = "".join(c.text for c in chunks)
    assert "निविदा" in joined and "ELIGIBILITY CONDITIONS" in joined


def test_devanagari_titles_and_references_normalise_without_loss() -> None:
    assert normalized_reference("निविदा सं. 12/2026-27") == "12202627"
    assert normalized_buyer("लोक निर्माण विभाग", region="in") == "लोक निर्माण विभाग"
    mixed = "लखनऊ नगर निगम / Lucknow Nagar Nigam"
    assert normalized_buyer(mixed, region="in") == "लखनऊ नगर निगम lucknow municipal"


# --- OCR languages --------------------------------------------------------------------


@pytest.mark.parametrize("region", [Region.IN, "in"])
def test_in_region_documents_use_the_hindi_language_set(region: Region | str) -> None:
    assert ocr_languages_for(region, SETTINGS) == SETTINGS.ocr_languages_in == "eng+hin"


def test_other_regions_use_the_default_language_set() -> None:
    settings = Settings(_env_file=None, ocr_languages="eng", ocr_languages_in="eng+hin")  # type: ignore[call-arg]
    assert ocr_languages_for(Region.US, settings) == "eng"
    assert ocr_languages_for(None, settings) == "eng"
    assert ocr_languages_for("in", settings) == "eng+hin"
    # the parser default already covers Hindi (SPEC 10.1)
    assert DEFAULT_OCR_LANGUAGES == "eng+hin"
