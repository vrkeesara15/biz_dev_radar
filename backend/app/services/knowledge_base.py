"""Tenant knowledge base (SPEC 4.3 RAG knowledge base, SPEC 8 "RAG indexes are per tenant").

    result = await index_profile(session, profile_id, embeddings=..., storage=router)
    hits = await similarity_search(session, profile_id, "cloud migration", k=8)
    hits[0].chunk.text, hits[0].chunk.page, hits[0].score          # cosine similarity

Sources: profile_files (capability statements, brochures, case studies, past proposals:
parsed through core.parsing from Storage bytes, page-tagged), boilerplate_blocks (HTML ->
text), past_performance and service_lines (text assembled from their fields). Each source
carries a content_hash; `index_profile` re-chunks and re-embeds only the sources whose hash
changed and removes chunks of sources that no longer exist. Chunking reuses
core.parsing.chunk_pages (M2-12); embeddings come from the process EmbeddingProvider.

`session` must be the tenant's session: RLS scopes every read and write to the tenant, so a
search for another tenant's profile returns nothing (isolation test).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.html_text import html_to_text
from app.core.parsing import OCR, Page, ParseError, chunk_pages, parse_document
from app.core.profile_fields import ProfileFileKind
from app.models import (
    BoilerplateBlock,
    CompanyProfile,
    File,
    KBChunk,
    PastPerformance,
    ProfileFile,
    ServiceLine,
)
from app.services.embeddings import EmbeddingProvider, get_embeddings
from app.services.storage import StorageRouter, get_storage_router

log = structlog.get_logger(__name__)

DEFAULT_K = 8
# profile file kinds that hold company knowledge (brand assets and templates do not)
INDEXED_FILE_KINDS = frozenset(
    {
        ProfileFileKind.CAPABILITY_STATEMENT,
        ProfileFileKind.BROCHURE,
        ProfileFileKind.CASE_STUDY,
        ProfileFileKind.PAST_PROPOSAL,
    }
)
PARSED_FILE_KINDS = frozenset({"pdf", "docx", "xlsx"})
TEXT_FILE_KINDS = frozenset({"text"})


class SourceType(StrEnum):
    PROFILE_FILE = "profile_file"
    BOILERPLATE = "boilerplate"
    PAST_PERFORMANCE = "past_performance"
    SERVICE_LINE = "service_line"


PageLoader = Callable[[], Awaitable[list[Page]]]


@dataclass(slots=True)
class KBSource:
    source_type: SourceType
    source_id: uuid.UUID
    title: str
    content_hash: str
    # pages are loaded lazily so unchanged files are never downloaded or parsed again
    load: PageLoader

    @property
    def key(self) -> tuple[str, uuid.UUID]:
        return (self.source_type.value, self.source_id)


@dataclass(slots=True)
class KBHit:
    chunk: KBChunk
    score: float  # cosine similarity in [-1, 1]; higher is closer

    @property
    def source_type(self) -> str:
        return self.chunk.source_type

    @property
    def citation(self) -> str:
        page = f" p.{self.chunk.page}" if self.chunk.page else ""
        return f"{self.chunk.source_type}:{self.chunk.source_id}#{self.chunk.chunk_index}{page}"


@dataclass(slots=True)
class IndexResult:
    profile_id: uuid.UUID
    indexed: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    unchanged: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    removed: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    chunks: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": str(self.profile_id),
            "indexed": len(self.indexed),
            "unchanged": len(self.unchanged),
            "removed": len(self.removed),
            "chunks": self.chunks,
            "warnings": list(self.warnings),
        }


# --- source text ------------------------------------------------------------------------------


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(*parts: tuple[str, Any]) -> str:
    out: list[str] = []
    for label, value in parts:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list | tuple):
            value = ", ".join(str(v) for v in value if str(v).strip())
            if not value:
                continue
        out.append(f"{label}: {value}" if label else str(value))
    return "\n".join(out)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def boilerplate_text(row: BoilerplateBlock) -> str:
    body = html_to_text(row.body) if row.body_format == "html" else (row.body or "")
    kind = str(row.kind.value if hasattr(row.kind, "value") else row.kind).replace("_", " ")
    return _lines(("", row.title), ("Boilerplate", kind)) + "\n\n" + body


def past_performance_text(row: PastPerformance) -> str:
    period = None
    if row.period_start or row.period_end:
        period = f"{row.period_start or '?'} to {row.period_end or 'present'}"
    value = None
    if row.value_amount is not None:
        value = f"{row.value_amount} {row.value_currency or ''}".strip()
    role = row.role.value if hasattr(row.role, "value") else str(row.role)
    agency = _enum_value(row.agency_type)
    cpars = _enum_value(row.cpars_rating)
    header = _lines(
        ("Past performance", row.title),
        ("Customer", row.customer),
        ("Agency type", agency),
        ("Role", role),
        ("Period", period),
        ("Value", value),
        ("NAICS", row.naics),
        ("Contract number", row.contract_number),
        ("Technologies", list(row.technologies or [])),
        ("CPARS", cpars),
    )
    return _lines(("", header), ("Scope", row.scope), ("Outcomes", row.outcomes))


def service_line_text(row: ServiceLine) -> str:
    delivery = _enum_value(row.delivery_model)
    return _lines(
        ("Service line", row.name),
        ("Description", row.description),
        ("Differentiators", list(row.differentiators or [])),
        ("Tools", list(row.tools or [])),
        ("Delivery model", delivery),
    )


def _text_source(source_type: SourceType, row_id: uuid.UUID, title: str, text: str) -> KBSource:
    pages = [Page(number=1, text=text)]

    async def load() -> list[Page]:
        return pages

    return KBSource(source_type, row_id, title, _hash(text), load)


def profile_file_source(
    row: ProfileFile, file: File, *, storage: StorageRouter, ocr: OCR | None
) -> KBSource:
    """Lazy source over the stored object; the hash is derived from the file's sha256 so an
    unchanged file costs no download."""
    title = row.title or file.filename
    kind = row.kind.value if hasattr(row.kind, "value") else str(row.kind)
    content_hash = _hash(f"{file.sha256}|{kind}|{title}")

    async def load() -> list[Page]:
        data = await storage.for_region(file.region).get(file.key)
        if file.kind in TEXT_FILE_KINDS:
            return [Page(number=1, text=data.decode("utf-8", errors="replace"))]
        parsed = parse_document(data, file_name=file.filename, mime_type=file.content_type, ocr=ocr)
        return parsed.pages

    return KBSource(SourceType.PROFILE_FILE, row.id, title, content_hash, load)


async def load_sources(
    session: AsyncSession,
    profile: CompanyProfile,
    *,
    storage: StorageRouter,
    ocr: OCR | None = None,
) -> tuple[list[KBSource], list[str]]:
    """Every indexable source of the profile (files that cannot be parsed are reported)."""
    pid = profile.id
    sources: list[KBSource] = []
    warnings: list[str] = []
    files = (
        await session.execute(
            select(ProfileFile, File)
            .join(File, File.id == ProfileFile.file_id)
            .where(ProfileFile.profile_id == pid)
            .order_by(ProfileFile.created_at, ProfileFile.id)
        )
    ).all()
    for profile_file, file in files:
        if profile_file.kind not in INDEXED_FILE_KINDS:
            continue
        if file.kind not in PARSED_FILE_KINDS | TEXT_FILE_KINDS:
            warnings.append(f"profile_file {profile_file.id}: {file.kind} files are not indexed")
            continue
        sources.append(profile_file_source(profile_file, file, storage=storage, ocr=ocr))
    for row in (
        (
            await session.execute(
                select(BoilerplateBlock)
                .where(BoilerplateBlock.profile_id == pid)
                .order_by(BoilerplateBlock.created_at, BoilerplateBlock.id)
            )
        )
        .scalars()
        .all()
    ):
        sources.append(
            _text_source(SourceType.BOILERPLATE, row.id, row.title, boilerplate_text(row))
        )
    for pp in (
        (
            await session.execute(
                select(PastPerformance)
                .where(PastPerformance.profile_id == pid)
                .order_by(PastPerformance.created_at, PastPerformance.id)
            )
        )
        .scalars()
        .all()
    ):
        sources.append(
            _text_source(SourceType.PAST_PERFORMANCE, pp.id, pp.title, past_performance_text(pp))
        )
    for line in (
        (
            await session.execute(
                select(ServiceLine)
                .where(ServiceLine.profile_id == pid)
                .order_by(ServiceLine.created_at, ServiceLine.id)
            )
        )
        .scalars()
        .all()
    ):
        sources.append(
            _text_source(SourceType.SERVICE_LINE, line.id, line.name, service_line_text(line))
        )
    return sources, warnings


# --- indexing ---------------------------------------------------------------------------------


async def _existing_hashes(
    session: AsyncSession, profile_id: uuid.UUID
) -> dict[tuple[str, uuid.UUID], str]:
    rows = await session.execute(
        select(KBChunk.source_type, KBChunk.source_id, KBChunk.content_hash)
        .where(KBChunk.profile_id == profile_id)
        .distinct()
    )
    return {(str(t), s): h for t, s, h in rows.all()}


async def _delete_source(session: AsyncSession, source_type: str, source_id: uuid.UUID) -> None:
    await session.execute(
        delete(KBChunk).where(KBChunk.source_type == source_type, KBChunk.source_id == source_id)
    )


async def index_source(
    session: AsyncSession,
    profile: CompanyProfile,
    source: KBSource,
    *,
    embeddings: EmbeddingProvider | None = None,
    force: bool = False,
    existing_hash: str | None = None,
) -> int | None:
    """(Re-)chunk and embed one source. Returns the number of chunks written, or None when
    the stored chunks already carry the same content_hash (nothing embedded)."""
    provider = embeddings or get_embeddings()
    if existing_hash is None:
        existing = await _existing_hashes(session, profile.id)
        existing_hash = existing.get(source.key, "")
    if not force and existing_hash == source.content_hash:
        return None
    pages = await source.load()
    chunks = chunk_pages(pages)
    await _delete_source(session, source.source_type.value, source.source_id)
    if not chunks:
        await session.flush()
        return 0
    vectors = await provider.embed([c.text for c in chunks], input_type="document")
    for chunk, vector in zip(chunks, vectors, strict=True):
        session.add(
            KBChunk(
                tenant_id=profile.tenant_id,
                profile_id=profile.id,
                source_type=source.source_type.value,
                source_id=source.source_id,
                chunk_index=chunk.index,
                text=chunk.text,
                page=chunk.page,
                embedding=vector,
                content_hash=source.content_hash,
            )
        )
    await session.flush()
    return len(chunks)


async def index_profile(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    embeddings: EmbeddingProvider | None = None,
    storage: StorageRouter | None = None,
    ocr: OCR | None = None,
    force: bool = False,
) -> IndexResult:
    """Bring kb_chunks in line with the profile: embed new/changed sources, keep unchanged
    ones, drop chunks whose source row is gone. A source that fails to parse is reported in
    `warnings` and its previous chunks (if any) are kept."""
    result = IndexResult(profile_id=profile_id)
    profile = await session.get(CompanyProfile, profile_id)
    if profile is None:
        result.warnings.append("profile not found")
        return result
    provider = embeddings or get_embeddings()
    sources, warnings = await load_sources(
        session, profile, storage=storage or get_storage_router(), ocr=ocr
    )
    result.warnings.extend(warnings)
    existing = await _existing_hashes(session, profile_id)
    current_keys = {s.key for s in sources}
    for source in sources:
        try:
            written = await index_source(
                session,
                profile,
                source,
                embeddings=provider,
                force=force,
                existing_hash=existing.get(source.key, ""),
            )
        except (ParseError, UnicodeDecodeError, OSError, KeyError) as exc:
            result.warnings.append(f"{source.source_type.value} {source.source_id}: {exc}")
            log.warning(
                "kb.source_failed",
                profile_id=str(profile_id),
                source_type=source.source_type.value,
                source_id=str(source.source_id),
                error=str(exc)[:300],
            )
            continue
        if written is None:
            result.unchanged.append(source.key)
        else:
            result.indexed.append(source.key)
            result.chunks += written
    for key in existing:
        if key not in current_keys:
            await _delete_source(session, key[0], key[1])
            result.removed.append(key)
    await session.flush()
    log.info("kb.indexed", **result.as_dict())
    return result


# --- search -----------------------------------------------------------------------------------


async def similarity_search(
    session: AsyncSession,
    profile_id: uuid.UUID,
    query: str,
    *,
    k: int = DEFAULT_K,
    source_types: Sequence[SourceType | str] | None = None,
    embeddings: EmbeddingProvider | None = None,
) -> list[KBHit]:
    """Top-k chunks of the profile by cosine similarity to `query` (RLS keeps it inside the
    tenant). `source_types` narrows to e.g. past_performance for the drafters."""
    if k <= 0 or not query.strip():
        return []
    provider = embeddings or get_embeddings()
    vector = (await provider.embed([query], input_type="query"))[0]
    distance = KBChunk.embedding.cosine_distance(vector).label("distance")
    stmt = select(KBChunk, distance).where(KBChunk.profile_id == profile_id)
    if source_types:
        stmt = stmt.where(KBChunk.source_type.in_([str(SourceType(str(t))) for t in source_types]))
    stmt = stmt.order_by(distance, KBChunk.source_id, KBChunk.chunk_index).limit(k)
    rows = (await session.execute(stmt)).all()
    return [KBHit(chunk=row[0], score=1.0 - float(row[1])) for row in rows]


async def chunk_count(session: AsyncSession, profile_id: uuid.UUID) -> int:
    from sqlalchemy import func

    total = (
        await session.execute(
            select(func.count()).select_from(KBChunk).where(KBChunk.profile_id == profile_id)
        )
    ).scalar_one()
    return int(total)
