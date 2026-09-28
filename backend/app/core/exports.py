"""Export naming, footers and the shape of an export package (SPEC 8, 11). Pure.

    naming = ExportNaming(template="{solicitation_number}_{volume}_{company}.docx", ...)
    naming.file_name("docx", volume="Volume I - Technical")
        "W91QUZ-26-R-0001_Volume-I-Technical_Alpha-Federal-LLC.docx"

    footer_lines(source_id="sam_opps", source_url=..., final=False)
        ["DRAFT - internal - not for submission", "AI-generated draft. Review before use.",
         "Source: SAM.gov * Official notice: https://... * Verify every detail ..."]

SPEC 11: "the export carries an internal footer until a human marks it final" and
"Verify every detail on the official portal before submitting" is on every export. Both
come from here, so DOCX, PDF, XLSX and the ZIP manifest cannot word them differently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.disclaimers import AI_DRAFT, record_footer

FORMAT_DOCX = "docx"
FORMAT_PDF = "pdf"
FORMAT_XLSX = "xlsx"
FORMAT_ZIP = "zip"
FORMATS: tuple[str, ...] = (FORMAT_DOCX, FORMAT_PDF, FORMAT_XLSX, FORMAT_ZIP)

CONTENT_TYPES: dict[str, str] = {
    FORMAT_DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FORMAT_PDF: "application/pdf",
    FORMAT_XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FORMAT_ZIP: "application/zip",
}

DRAFT_FOOTER = "DRAFT - internal - not for submission"
DEFAULT_TEMPLATE = "{solicitation_number}_{volume}_{company}.{ext}"
MAX_NAME_CHARS = 120

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]{1,40})\}")
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DASHES_RE = re.compile(r"-{2,}")


def sanitize_segment(value: str, *, fallback: str = "untitled") -> str:
    """One path-safe filename segment: no separators, no spaces, no control characters."""
    text = _UNSAFE_RE.sub("-", (value or "").strip()).strip("-._")
    text = _DASHES_RE.sub("-", text)
    return text[:MAX_NAME_CHARS] or fallback


def sanitize_filename(value: str, *, extension: str, fallback: str = "export") -> str:
    """A safe `<stem>.<ext>`; the extension always wins over whatever the name claimed."""
    ext = sanitize_segment(extension, fallback="bin").lower().lstrip(".")
    stem = (value or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if stem.lower().endswith(f".{ext}"):
        stem = stem[: -(len(ext) + 1)]
    return f"{sanitize_segment(stem, fallback=fallback)}.{ext}"


@dataclass(frozen=True, slots=True)
class ExportNaming:
    """The solicitation's file-naming rule (agent 3's `format_rules.file_naming`).

    Unknown placeholders are dropped rather than guessed, and the result is always
    sanitised: a naming rule copied out of a PDF is untrusted text.
    """

    company: str = "company"
    solicitation_number: str | None = None
    title: str | None = None
    template: str | None = None

    def values(self, *, volume: str | None = None, ext: str = "docx") -> dict[str, str]:
        return {
            "company": sanitize_segment(self.company, fallback="company"),
            "solicitation_number": sanitize_segment(
                self.solicitation_number or self.title or "solicitation", fallback="solicitation"
            ),
            "title": sanitize_segment(self.title or "proposal", fallback="proposal"),
            "volume": sanitize_segment(volume or "package", fallback="package"),
            "ext": ext.lstrip("."),
        }

    def file_name(self, ext: str, *, volume: str | None = None) -> str:
        values = self.values(volume=volume, ext=ext)
        template = self.template or DEFAULT_TEMPLATE
        if "{ext}" not in template and "." not in template:
            template = f"{template}.{{ext}}"
        rendered = _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), template)
        return sanitize_filename(rendered, extension=ext, fallback=values["solicitation_number"])


def footer_lines(
    *, source_id: str, source_url: str | None = None, final: bool = False, ai_draft: bool = True
) -> list[str]:
    """What every exported artifact carries at the bottom (SPEC 11 product disclaimers)."""
    lines: list[str] = []
    if not final:
        lines.append(DRAFT_FOOTER)
    if ai_draft:
        lines.append(AI_DRAFT)
    lines.append(record_footer(source_id, source_url))
    return lines


def footer_text(
    *, source_id: str, source_url: str | None = None, final: bool = False, separator: str = "  |  "
) -> str:
    return separator.join(footer_lines(source_id=source_id, source_url=source_url, final=final))


# --- the package an exporter renders ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExportSection:
    """One draft section in outline order."""

    section_id: str
    title: str
    volume: str | None = None
    body_text: str = ""
    body_html: str = ""
    citations: tuple[str, ...] = ()
    needs_input: tuple[str, ...] = ()
    status: str = "draft"
    version: int = 0
    unsupported_claims: int = 0


@dataclass(frozen=True, slots=True)
class ExportMatrixRow:
    req_id: str
    text: str
    type: str
    page: int
    section: str
    owner: str = ""
    status: str = "open"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ExportChecklistRow:
    key: str
    label: str
    category: str = ""
    required: bool = True
    note: str = ""
    source_req_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExportPackage:
    """Everything a renderer needs; assembled by `app.services.exports.load_package`."""

    company: str
    title: str
    solicitation_number: str | None = None
    source_id: str = ""
    source_url: str | None = None
    region: str = "us"
    final: bool = False
    naming: ExportNaming = field(default_factory=ExportNaming)
    sections: tuple[ExportSection, ...] = ()
    matrix: tuple[ExportMatrixRow, ...] = ()
    checklist: tuple[ExportChecklistRow, ...] = ()
    # the pricing template the pricing agent produced, as raw XLSX bytes (None = none yet)
    pricing_xlsx: bytes | None = None
    # the tenant's uploaded .docx template and brand logo, when they have one
    template_docx: bytes | None = None
    logo_png: bytes | None = None
    logo_name: str | None = None

    @property
    def volumes(self) -> list[str]:
        seen: list[str] = []
        for section in self.sections:
            name = section.volume or "Proposal"
            if name not in seen:
                seen.append(name)
        return seen

    def footer(self) -> str:
        return footer_text(source_id=self.source_id, source_url=self.source_url, final=self.final)

    def footers(self) -> list[str]:
        return footer_lines(source_id=self.source_id, source_url=self.source_url, final=self.final)

    def file_name(self, ext: str, *, volume: str | None = None) -> str:
        return self.naming.file_name(ext, volume=volume)


def zip_entries(package: ExportPackage) -> list[tuple[str, str]]:
    """(format, file name) of every member of the ZIP, in the order they are written."""
    entries = [(FORMAT_DOCX, package.file_name(FORMAT_DOCX, volume="Technical"))]
    entries.append((FORMAT_PDF, package.file_name(FORMAT_PDF, volume="Technical")))
    entries.append((FORMAT_XLSX, package.file_name(FORMAT_XLSX, volume="Compliance")))
    return entries
