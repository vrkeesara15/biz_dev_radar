"""M5-13: the pure half of exports -- the solicitation's file-naming rule and the
footers SPEC 11 puts on every artifact."""

from __future__ import annotations

import pytest
from app.core.disclaimers import AI_DRAFT, VERIFY_ON_PORTAL
from app.core.exports import (
    DRAFT_FOOTER,
    FORMATS,
    ExportNaming,
    ExportPackage,
    ExportSection,
    footer_lines,
    footer_text,
    sanitize_filename,
    sanitize_segment,
    zip_entries,
)

NAMING = ExportNaming(
    company="Alpha Federal LLC",
    solicitation_number="W91QUZ-26-R-0001",
    title="Cloud migration services",
)


def test_the_four_formats_are_fixed() -> None:
    assert FORMATS == ("docx", "pdf", "xlsx", "zip")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alpha Federal LLC", "Alpha-Federal-LLC"),
        ("  spaced  out  ", "spaced-out"),
        ("../../etc/passwd", "etc-passwd"),
        ("a/b\\c", "a-b-c"),
        ("name\x00with\nnulls", "name-with-nulls"),
        ("...", "untitled"),
        ("", "untitled"),
        ("Volume I - Technical", "Volume-I-Technical"),
    ],
)
def test_a_segment_is_path_safe(raw: str, expected: str) -> None:
    assert sanitize_segment(raw) == expected


def test_a_file_name_always_gets_the_real_extension() -> None:
    assert sanitize_filename("report", extension="docx") == "report.docx"
    assert sanitize_filename("report.docx", extension="docx") == "report.docx"
    # a name that claims another type does not get to keep it
    assert sanitize_filename("report.exe", extension="pdf") == "report.exe.pdf"
    assert sanitize_filename("/tmp/../evil.sh", extension="zip") == "evil.sh.zip"


def test_the_default_naming_rule() -> None:
    assert (
        NAMING.file_name("docx", volume="Volume I - Technical")
        == "W91QUZ-26-R-0001_Volume-I-Technical_Alpha-Federal-LLC.docx"
    )
    assert NAMING.file_name("zip") == "W91QUZ-26-R-0001_package_Alpha-Federal-LLC.zip"


def test_the_solicitations_own_rule_wins() -> None:
    naming = ExportNaming(
        company="Alpha Federal LLC",
        solicitation_number="W91QUZ-26-R-0001",
        template="{company}-{solicitation_number}-TECH",
    )
    assert naming.file_name("pdf") == "Alpha-Federal-LLC-W91QUZ-26-R-0001-TECH.pdf"


def test_an_unknown_placeholder_is_dropped_not_guessed() -> None:
    naming = ExportNaming(company="Alpha", template="{solicitation_number}_{cage_code}.{ext}")
    # the placeholder vanishes and the separator it left behind is trimmed
    assert naming.file_name("docx") == "solicitation.docx"


def test_a_naming_rule_copied_out_of_a_pdf_cannot_escape_the_archive() -> None:
    """The rule is untrusted text: it is a filename, never a path."""
    naming = ExportNaming(company="Alpha", template="../../../etc/{company}")
    name = naming.file_name("docx")
    assert "/" not in name and ".." not in name
    assert name.endswith(".docx")


def test_a_notice_with_no_solicitation_number_falls_back_to_its_title() -> None:
    naming = ExportNaming(company="Beta Systems", title="Supply of desktop computers")
    assert naming.file_name("xlsx") == "Supply-of-desktop-computers_package_Beta-Systems.xlsx"


def test_a_draft_export_says_so_and_a_final_one_does_not() -> None:
    draft = footer_lines(source_id="sam_opps", source_url="https://sam.gov/opp/1")
    assert draft[0] == DRAFT_FOOTER
    assert AI_DRAFT in draft
    assert VERIFY_ON_PORTAL in draft[-1]
    assert "sam.gov" in draft[-1].lower()

    final = footer_lines(source_id="sam_opps", source_url="https://sam.gov/opp/1", final=True)
    assert DRAFT_FOOTER not in final
    assert VERIFY_ON_PORTAL in final[-1], "the portal disclaimer is on EVERY export"
    assert AI_DRAFT in final


def test_the_footer_is_one_line_for_a_spreadsheet_cell() -> None:
    text = footer_text(source_id="gem", source_url="https://gem.gov.in/x")
    assert "\n" not in text
    assert DRAFT_FOOTER in text and VERIFY_ON_PORTAL in text


def _package(**kwargs: object) -> ExportPackage:
    base: dict[str, object] = {
        "company": "Alpha Federal LLC",
        "title": "Cloud migration services",
        "solicitation_number": "W91QUZ-26-R-0001",
        "source_id": "sam_opps",
        "source_url": "https://sam.gov/opp/1",
        "naming": NAMING,
        "sections": (
            ExportSection("technical-approach", "Technical Approach", "Volume I - Technical"),
            ExportSection("quality", "Quality", "Volume I - Technical"),
            ExportSection("past-performance", "Past Performance", "Volume II"),
        ),
    }
    base.update(kwargs)
    return ExportPackage(**base)  # type: ignore[arg-type]


def test_the_package_lists_its_volumes_in_order_without_repeats() -> None:
    assert _package().volumes == ["Volume I - Technical", "Volume II"]
    assert ExportPackage(company="a", title="b").volumes == []


def test_the_package_footer_follows_the_final_flag() -> None:
    assert DRAFT_FOOTER in _package().footer()
    assert DRAFT_FOOTER not in _package(final=True).footer()


def test_the_zip_holds_the_three_named_artifacts() -> None:
    entries = zip_entries(_package())
    assert [fmt for fmt, _ in entries] == ["docx", "pdf", "xlsx"]
    assert entries[0][1].endswith(".docx") and "Technical" in entries[0][1]
    assert entries[2][1].endswith(".xlsx") and "Compliance" in entries[2][1]
