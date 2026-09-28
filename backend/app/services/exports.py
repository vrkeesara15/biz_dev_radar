"""Export rendering and storage (SPEC 8 "Export: DOCX in the tenant's template, PDF,
XLSX (matrix, pricing), and a ZIP named per the solicitation's file-naming rules").

    package = await load_package(session, pursuit, storage=storage)
    row, url = await create_export(session, tenant_id, pursuit, "docx", package, storage=storage)

Rendering is deterministic and offline:

- DOCX (python-docx) from the tenant's uploaded .docx template (`profile_files` kind
  `template`) or a default built in memory; sections in outline order with headings,
  citations rendered as bracketed references, `[NEEDS INPUT: ...]` highlighted yellow,
  a cover page with the brand logo when one is uploaded.
- PDF from that DOCX through LibreOffice (`soffice --headless --convert-to pdf`) when it
  is on PATH, otherwise a pure-Python fallback that lays the text out with PyMuPDF. The
  path actually used is recorded on the export row, so a deployment without LibreOffice
  is visible rather than silent.
- XLSX (openpyxl) with a Matrix sheet, a Checklist sheet and the pricing template copied
  in as a Pricing sheet.
- ZIP of the three, named by `core.exports.ExportNaming`.

Every artifact carries the "DRAFT - internal" footer until `pursuits.package_final` and
always the verify-on-portal disclaimer (`core.disclaimers.record_footer`).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compliance import (
    ARTIFACT_CHECKLIST,
    ARTIFACT_FORMAT_RULES,
    ARTIFACT_OUTLINE,
    ARTIFACT_PRICING_TEMPLATE,
    ChecklistItem,
    FormatRules,
)
from app.core.exports import (
    CONTENT_TYPES,
    FORMAT_DOCX,
    FORMAT_PDF,
    FORMAT_XLSX,
    FORMAT_ZIP,
    FORMATS,
    ExportChecklistRow,
    ExportMatrixRow,
    ExportNaming,
    ExportPackage,
    ExportSection,
)
from app.core.outline import Outline
from app.core.paths import safe_segment
from app.core.profile_fields import ProfileFileKind
from app.models import (
    CompanyProfile,
    ComplianceItem,
    Draft,
    DraftVersion,
    Export,
    File,
    Opportunity,
    ProfileFile,
    Pursuit,
    PursuitArtifact,
    Requirement,
)
from app.services.storage import Storage

log = structlog.get_logger(__name__)

RENDERER_LIBREOFFICE = "libreoffice"
RENDERER_PYMUPDF = "pymupdf"
SOFFICE_TIMEOUT_SECONDS = 120
NEEDS_INPUT_MARKER = "[NEEDS INPUT"
YELLOW = "FFF2CC"
HEADER_GREY = "D9D9D9"
MAX_LOGO_BYTES = 2_000_000


def export_key(tenant_id: uuid.UUID, pursuit_id: uuid.UUID, export_id: uuid.UUID, ext: str) -> str:
    """tenants/{tenant}/pursuits/{pursuit}/exports/{export}.{ext}"""
    suffix = safe_segment(ext.lstrip("."))
    return (
        f"tenants/{safe_segment(str(tenant_id))}/pursuits/{safe_segment(str(pursuit_id))}"
        f"/exports/{safe_segment(str(export_id))}.{suffix}"
    )


# --- loading the package ------------------------------------------------------------------


async def _artifact(
    session: AsyncSession, pursuit_id: uuid.UUID, kind: str
) -> PursuitArtifact | None:
    return (
        await session.execute(
            select(PursuitArtifact)
            .where(PursuitArtifact.pursuit_id == pursuit_id, PursuitArtifact.kind == kind)
            .order_by(PursuitArtifact.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _profile_file(
    session: AsyncSession, profile_id: uuid.UUID, kind: ProfileFileKind
) -> tuple[File, ProfileFile] | None:
    row = (
        await session.execute(
            select(ProfileFile, File)
            .join(File, File.id == ProfileFile.file_id)
            .where(ProfileFile.profile_id == profile_id, ProfileFile.kind == kind)
            .order_by(ProfileFile.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[1], row[0]


async def _read_object(storage: Storage | None, key: str) -> bytes | None:
    if storage is None:
        return None
    try:
        return await storage.get(key)
    except Exception as exc:  # a missing or unreadable asset never fails an export
        log.warning("exports.asset_unreadable", key=key, error=str(exc))
        return None


def _section_order(outline: Outline) -> dict[str, int]:
    return {section.id: index for index, (_v, section) in enumerate(outline.sections())}


async def load_package(
    session: AsyncSession,
    pursuit: Pursuit,
    *,
    storage: Storage | None = None,
) -> ExportPackage:
    """Assemble everything the renderers need from the pursuit's stored state."""
    opportunity = await session.get(Opportunity, pursuit.opportunity_id)
    profile = await session.get(CompanyProfile, pursuit.profile_id)
    if opportunity is None or profile is None:  # pragma: no cover - FKs guarantee both
        raise LookupError("the pursuit's opportunity or profile is missing")

    outline_row = await _artifact(session, pursuit.id, ARTIFACT_OUTLINE)
    outline_data = (outline_row.data or {}) if outline_row is not None else {}
    outline = Outline.model_validate(outline_data.get("outline") or {})
    order = _section_order(outline)
    rules_row = await _artifact(session, pursuit.id, ARTIFACT_FORMAT_RULES)
    rules = FormatRules() if rules_row is None else FormatRules.model_validate(rules_row.data)
    checklist_row = await _artifact(session, pursuit.id, ARTIFACT_CHECKLIST)
    checklist = [
        ChecklistItem.model_validate(item)
        for item in ((checklist_row.data or {}).get("items", []) if checklist_row else [])
    ]

    rows = (
        await session.execute(
            select(Draft, DraftVersion)
            .outerjoin(DraftVersion, DraftVersion.id == Draft.current_version_id)
            .where(Draft.pursuit_id == pursuit.id)
        )
    ).all()
    sections: list[ExportSection] = []
    for draft, version in rows:
        sections.append(
            ExportSection(
                section_id=draft.section_id,
                title=draft.title,
                volume=draft.volume,
                body_text="" if version is None else version.body_text,
                body_html="" if version is None else version.body_html,
                citations=()
                if version is None
                else tuple(
                    str(c.get("token"))
                    for c in (version.citations or [])
                    if isinstance(c, dict) and c.get("token")
                ),
                needs_input=()
                if version is None
                else tuple(
                    str(n.get("placeholder"))
                    for n in (version.needs_input or [])
                    if isinstance(n, dict) and n.get("placeholder")
                ),
                status=draft.status,
                version=0 if version is None else version.version,
                unsupported_claims=0
                if version is None
                else int((version.flags or {}).get("unsupported_count") or 0),
            )
        )
    sections.sort(key=lambda s: (order.get(s.section_id, 10_000), s.section_id))

    matrix_rows = (
        await session.execute(
            select(ComplianceItem, Requirement)
            .join(Requirement, Requirement.id == ComplianceItem.requirement_id)
            .where(ComplianceItem.pursuit_id == pursuit.id)
            .order_by(Requirement.req_id)
        )
    ).all()

    pricing_row = await _artifact(session, pursuit.id, ARTIFACT_PRICING_TEMPLATE)
    pricing_key = (pricing_row.data or {}).get("storage_key") if pricing_row else None
    pricing = await _read_object(storage, str(pricing_key)) if pricing_key else None

    template_bytes: bytes | None = None
    template = await _profile_file(session, profile.id, ProfileFileKind.TEMPLATE)
    if template is not None:
        template_bytes = await _read_object(storage, template[0].key)
    logo_bytes: bytes | None = None
    logo_name: str | None = None
    brand = await _profile_file(session, profile.id, ProfileFileKind.BRAND)
    if brand is not None and brand[0].kind in ("png", "jpeg"):
        candidate = await _read_object(storage, brand[0].key)
        if candidate is not None and len(candidate) <= MAX_LOGO_BYTES:
            logo_bytes, logo_name = candidate, brand[0].filename

    return ExportPackage(
        company=profile.legal_name,
        title=opportunity.title,
        solicitation_number=opportunity.solicitation_number,
        source_id=str(opportunity.source_id),
        source_url=opportunity.source_url,
        region=str(opportunity.region),
        final=bool(pursuit.package_final),
        naming=ExportNaming(
            company=profile.legal_name,
            solicitation_number=opportunity.solicitation_number,
            title=opportunity.title,
            template=rules.file_naming,
        ),
        sections=tuple(sections),
        matrix=tuple(
            ExportMatrixRow(
                req_id=req.req_id,
                text=req.text,
                type=req.type,
                page=req.page,
                section=item.section,
                owner="" if item.owner_user_id is None else str(item.owner_user_id),
                status=item.status,
                notes=item.notes or "",
            )
            for item, req in matrix_rows
        ),
        checklist=tuple(
            ExportChecklistRow(
                key=item.key,
                label=item.label,
                category=item.category,
                required=item.required,
                note=item.note or "",
                source_req_ids=tuple(item.source_req_ids),
            )
            for item in checklist
        ),
        pricing_xlsx=pricing,
        template_docx=template_bytes,
        logo_png=logo_bytes,
        logo_name=logo_name,
    )


# --- DOCX ------------------------------------------------------------------------------------


def default_template_bytes() -> bytes:
    """A plain proposal template built in memory (no binary asset in the repo)."""
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _open_template(package: ExportPackage) -> Any:
    if package.template_docx:
        try:
            return Document(io.BytesIO(package.template_docx))
        except Exception as exc:  # a broken upload must not break the export
            log.warning("exports.template_unreadable", error=str(exc))
    return Document(io.BytesIO(default_template_bytes()))


def _add_footer(document: Any, package: ExportPackage) -> None:
    """The internal footer + disclaimer on every page of every section."""
    for section in document.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        paragraph.text = ""
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for index, line in enumerate(package.footers()):
            run = paragraph.add_run(("\n" if index else "") + line)
            run.font.size = Pt(7)
            run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            if index == 0 and not package.final:
                run.bold = True


def _cover_page(document: Any, package: ExportPackage) -> None:
    if package.logo_png:
        try:
            document.add_picture(io.BytesIO(package.logo_png), width=Inches(2.0))
        except Exception as exc:  # pragma: no cover - a corrupt image is not fatal
            log.warning("exports.logo_unreadable", error=str(exc))
    title = document.add_heading(package.title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for line in (
        package.company,
        f"Solicitation {package.solicitation_number}" if package.solicitation_number else "",
        "Proposal package" if package.final else "Proposal package (draft)",
    ):
        if not line:
            continue
        paragraph = document.add_paragraph(line)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_page_break()


def _write_body(document: Any, package: ExportPackage) -> None:
    current_volume: str | None = None
    for section in package.sections:
        volume = section.volume or "Proposal"
        if volume != current_volume:
            document.add_heading(volume, level=1)
            current_volume = volume
        document.add_heading(section.title, level=2)
        for line in (section.body_text or "").splitlines():
            text = line.strip()
            if not text:
                continue
            paragraph = document.add_paragraph()
            run = paragraph.add_run(text)
            if NEEDS_INPUT_MARKER in text:
                run.font.highlight_color = 7  # WD_COLOR_INDEX.YELLOW
                run.bold = True
        if section.citations:
            references = document.add_paragraph()
            run = references.add_run(
                "References: " + "; ".join(f"[{c}]" for c in section.citations)
            )
            run.font.size = Pt(8)
            run.italic = True
    if not package.sections:
        document.add_paragraph("No sections have been drafted yet.")


def build_docx(package: ExportPackage) -> bytes:
    """The proposal in the tenant's template, sections in outline order."""
    document = _open_template(package)
    _cover_page(document, package)
    _write_body(document, package)
    _add_footer(document, package)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- PDF -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderedPdf:
    data: bytes
    renderer: str


def soffice_path() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _libreoffice_pdf(docx_bytes: bytes) -> bytes | None:
    binary = soffice_path()
    if binary is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "proposal.docx"
        source.write_bytes(docx_bytes)
        try:
            # a fixed argument list, no shell, and only paths this function created
            subprocess.run(
                [
                    binary,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmp,
                    str(source),
                ],
                check=True,
                capture_output=True,
                timeout=SOFFICE_TIMEOUT_SECONDS,
                env={"HOME": tmp, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("exports.libreoffice_failed", error=str(exc))
            return None
        out = Path(tmp) / "proposal.pdf"
        return out.read_bytes() if out.exists() else None


def _pymupdf_pdf(package: ExportPackage) -> bytes:
    """Pure-Python fallback: lay the text out with PyMuPDF (already a dependency)."""
    import pymupdf

    rect: Any = pymupdf.Rect
    document: Any = pymupdf.open()  # type: ignore[no-untyped-call]
    margin, width, height = 56, 595, 842  # A4 in points
    body_width = width - 2 * margin

    def page_with(title: str, lines: list[str]) -> None:
        page = document.new_page(width=width, height=height)
        y = margin
        page.insert_textbox(
            rect(margin, y, width - margin, y + 40), title, fontsize=14, fontname="hebo"
        )
        y += 44
        for line in lines:
            box = rect(margin, y, margin + body_width, height - margin - 46)
            used = page.insert_textbox(box, line, fontsize=10, fontname="helv")
            if used < 0:  # the box was too small: continue on a new page
                page = document.new_page(width=width, height=height)
                y = margin
                page.insert_textbox(
                    rect(margin, y, width - margin, height - margin - 46),
                    line,
                    fontsize=10,
                    fontname="helv",
                )
                y = height - margin - 60
                continue
            y += max(14, (line.count("\n") + 1 + len(line) // 95) * 13)
            if y > height - margin - 70:
                page = document.new_page(width=width, height=height)
                y = margin

    cover = [package.company]
    if package.solicitation_number:
        cover.append(f"Solicitation {package.solicitation_number}")
    cover.append("Proposal package" if package.final else "Proposal package (draft)")
    page_with(package.title, cover)
    for section in package.sections:
        lines = [line for line in (section.body_text or "").splitlines() if line.strip()]
        if section.citations:
            lines.append("References: " + "; ".join(f"[{c}]" for c in section.citations))
        page_with(f"{section.volume or 'Proposal'} - {section.title}", lines or ["(not drafted)"])
    if not package.sections:
        page_with("Sections", ["No sections have been drafted yet."])
    footer = " | ".join(package.footers())
    for page in document:
        page.insert_textbox(
            rect(margin, height - margin - 40, width - margin, height - margin),
            footer,
            fontsize=6,
            fontname="helv",
        )
    data: bytes = document.tobytes()
    document.close()
    return data


def build_pdf(package: ExportPackage, docx_bytes: bytes | None = None) -> RenderedPdf:
    """PDF from the DOCX through LibreOffice when it is available, else PyMuPDF."""
    if docx_bytes is not None:
        rendered = _libreoffice_pdf(docx_bytes)
        if rendered:
            return RenderedPdf(rendered, RENDERER_LIBREOFFICE)
    return RenderedPdf(_pymupdf_pdf(package), RENDERER_PYMUPDF)


# --- XLSX ------------------------------------------------------------------------------------


def _header(sheet: Any, labels: list[str]) -> None:
    sheet.append(labels)
    fill = PatternFill("solid", fgColor=HEADER_GREY)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
    sheet.freeze_panes = "A2"


def build_xlsx(package: ExportPackage) -> bytes:
    """Matrix + Checklist + the pricing template, each with the footer on the sheet."""
    workbook = Workbook()
    matrix: Any = workbook.active
    matrix.title = "Matrix"
    _header(matrix, ["Requirement", "Type", "Page", "Text", "Section", "Owner", "Status", "Notes"])
    for row in package.matrix:
        matrix.append(
            [
                row.req_id,
                row.type,
                row.page,
                row.text,
                row.section,
                row.owner,
                row.status,
                row.notes,
            ]
        )
    for column, width in zip("ABCDEFGH", (14, 12, 8, 70, 26, 36, 12, 30), strict=False):
        matrix.column_dimensions[column].width = width
    matrix.append([])
    matrix.append([package.footer()])

    checklist: Any = workbook.create_sheet("Checklist")
    _header(checklist, ["Item", "Label", "Category", "Required", "Note", "Requirements"])
    for item in package.checklist:
        checklist.append(
            [
                item.key,
                item.label,
                item.category,
                "yes" if item.required else "no",
                item.note,
                ", ".join(item.source_req_ids),
            ]
        )
    for column, width in zip("ABCDEF", (26, 46, 14, 10, 60, 20), strict=False):
        checklist.column_dimensions[column].width = width
    checklist.append([])
    checklist.append([package.footer()])

    pricing: Any = workbook.create_sheet("Pricing")
    if package.pricing_xlsx:
        source = load_workbook(io.BytesIO(package.pricing_xlsx))
        for sheet in source.worksheets:
            pricing.append([f"--- {sheet.title} ---"])
            for values in sheet.iter_rows(values_only=True):
                pricing.append(["" if v is None else v for v in values])
        for cell in pricing["A"]:
            if isinstance(cell.value, str) and cell.value.startswith("[NEEDS INPUT"):
                cell.fill = PatternFill("solid", fgColor=YELLOW)
    else:
        pricing.append(["The pricing agent has not produced a template for this pursuit yet."])
    pricing.column_dimensions["A"].width = 44
    pricing.append([])
    pricing.append([package.footer()])
    for sheet in workbook.worksheets:
        sheet.cell(row=sheet.max_row, column=1).alignment = Alignment(wrap_text=False)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --- ZIP -------------------------------------------------------------------------------------


def build_zip(package: ExportPackage, *, docx: bytes, pdf: bytes, xlsx: bytes) -> bytes:
    """The submission package, named per the solicitation's file-naming rule."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(package.file_name(FORMAT_DOCX, volume="Technical"), docx)
        archive.writestr(package.file_name(FORMAT_PDF, volume="Technical"), pdf)
        archive.writestr(package.file_name(FORMAT_XLSX, volume="Compliance"), xlsx)
        archive.writestr("MANIFEST.txt", manifest_text(package))
    return buffer.getvalue()


def manifest_text(package: ExportPackage) -> str:
    lines = [
        f"Proposal package for {package.title}",
        f"Company: {package.company}",
        f"Solicitation: {package.solicitation_number or 'not stated'}",
        f"Status: {'final' if package.final else 'DRAFT - internal'}",
        "",
        "Files:",
        f"  {package.file_name(FORMAT_DOCX, volume='Technical')}  proposal sections",
        f"  {package.file_name(FORMAT_PDF, volume='Technical')}  the same, as PDF",
        f"  {package.file_name(FORMAT_XLSX, volume='Compliance')}  compliance matrix,"
        " checklist and pricing",
        "",
        "Sections:",
    ]
    lines.extend(
        f"  {section.volume or 'Proposal'} / {section.title} (v{section.version}, {section.status})"
        for section in package.sections
    )
    lines.extend(["", *package.footers()])
    return "\n".join(lines) + "\n"


# --- rendering + persistence ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderedExport:
    data: bytes
    content_type: str
    file_name: str
    renderer: str | None = None


def render(package: ExportPackage, fmt: str) -> RenderedExport:
    """Render one format. Pure apart from the optional LibreOffice subprocess."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format {fmt!r}; one of {FORMATS}")
    if fmt == FORMAT_DOCX:
        return RenderedExport(
            build_docx(package), CONTENT_TYPES[fmt], package.file_name(fmt, volume="Technical")
        )
    if fmt == FORMAT_XLSX:
        return RenderedExport(
            build_xlsx(package), CONTENT_TYPES[fmt], package.file_name(fmt, volume="Compliance")
        )
    if fmt == FORMAT_PDF:
        pdf = build_pdf(package, build_docx(package))
        return RenderedExport(
            pdf.data, CONTENT_TYPES[fmt], package.file_name(fmt, volume="Technical"), pdf.renderer
        )
    docx = build_docx(package)
    pdf = build_pdf(package, docx)
    xlsx = build_xlsx(package)
    return RenderedExport(
        build_zip(package, docx=docx, pdf=pdf.data, xlsx=xlsx),
        CONTENT_TYPES[FORMAT_ZIP],
        package.file_name(FORMAT_ZIP, volume="Package"),
        pdf.renderer,
    )


async def next_version(session: AsyncSession, pursuit_id: uuid.UUID, fmt: str) -> int:
    rows: list[int] = list(
        (
            await session.execute(
                select(Export.version).where(Export.pursuit_id == pursuit_id, Export.format == fmt)
            )
        )
        .scalars()
        .all()
    )
    return max(rows, default=0) + 1


async def create_export(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pursuit: Pursuit,
    fmt: str,
    package: ExportPackage,
    *,
    storage: Storage,
    created_by: uuid.UUID | None = None,
) -> Export:
    """Render, store the object and append the `exports` row."""
    rendered = render(package, fmt)
    row = Export(
        tenant_id=tenant_id,
        pursuit_id=pursuit.id,
        format=fmt,
        version=await next_version(session, pursuit.id, fmt),
        file_name=rendered.file_name,
        content_type=rendered.content_type,
        size_bytes=len(rendered.data),
        renderer=rendered.renderer,
        final=bool(pursuit.package_final),
        storage_key="",
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    row.storage_key = export_key(tenant_id, pursuit.id, row.id, fmt)
    await storage.put(row.storage_key, rendered.data, rendered.content_type)
    await session.flush()
    log.info(
        "exports.created",
        pursuit_id=str(pursuit.id),
        format=fmt,
        version=row.version,
        bytes=row.size_bytes,
        renderer=row.renderer,
        final=row.final,
    )
    return row


async def list_exports(session: AsyncSession, pursuit_id: uuid.UUID) -> list[Export]:
    return list(
        (
            await session.execute(
                select(Export)
                .where(Export.pursuit_id == pursuit_id)
                .order_by(Export.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_export(
    session: AsyncSession, pursuit_id: uuid.UUID, export_id: uuid.UUID
) -> Export | None:
    return (
        await session.execute(
            select(Export).where(Export.id == export_id, Export.pursuit_id == pursuit_id)
        )
    ).scalar_one_or_none()
