"""M2-12: parse_and_store writes text via Storage, fills the document row and chunks."""

from pathlib import Path
from typing import Any

import httpx
import respx
from app.adapters.http import MemoryArchiver, PoliteClient
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.paths import parsed_text_key
from app.core.politeness import PolicyTable
from app.models import DocumentChunk, Opportunity, OpportunityDocument
from app.services.documents import download_document, load_parsed_text, parse_and_store
from app.services.storage import LocalStorage
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tests.ocr_fake import FakeOCR

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"


async def _document(database: Database, url: str, file_name: str | None) -> tuple[Any, Any]:
    async with database.session(None) as session:
        opp = Opportunity(
            source_id="sam_opps",
            external_id="doc-test",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Docs",
        )
        session.add(opp)
        await session.flush()
        doc = OpportunityDocument(opportunity_id=opp.id, url=url, file_name=file_name)
        session.add(doc)
        await session.flush()
        return opp.id, doc.id


def _storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path, "bidradar-us", signing_secret="s")


async def test_parse_and_store_pdf_fills_row_text_and_chunks(
    database: Database, tmp_path: Path
) -> None:
    opp_id, doc_id = await _document(database, "https://sam.example/text.pdf", "text.pdf")
    storage = _storage(tmp_path)
    data = (FIXTURES / "text.pdf").read_bytes()
    async with database.session(None) as session:
        doc = await session.get(OpportunityDocument, doc_id)
        assert doc is not None
        parsed = await parse_and_store(session, doc, data, storage=storage, ocr=FakeOCR())
        assert parsed is not None and parsed.page_count == 3
    async with database.session(None) as session:
        doc = (
            await session.execute(
                select(OpportunityDocument)
                .options(selectinload(OpportunityDocument.chunks))
                .where(OpportunityDocument.id == doc_id)
            )
        ).scalar_one()
        assert doc.status == "parsed" and doc.pages == 3 and doc.size == len(data)
        assert doc.hash == parsed.sha256 and len(doc.hash) == 64
        assert (
            doc.parsed_text_ref
            == parsed_text_key(opp_id, doc_id)
            == f"parsed/{opp_id}/{doc_id}.txt"
        )
        assert doc.mime_type == "application/pdf" and doc.ocr_pages == 0 and doc.parse_error is None
        chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert chunks[0].page == 1 and all(c.embedding is None for c in chunks)
        assert "40 field offices" in chunks[0].text
        pages = await load_parsed_text(storage, doc)
    assert len(pages) == 3 and "FedRAMP Moderate" in pages[1]
    assert await storage.exists(f"parsed/{opp_id}/{doc_id}.txt")


async def test_reparse_replaces_chunks_and_scanned_uses_ocr(
    database: Database, tmp_path: Path
) -> None:
    _, doc_id = await _document(database, "https://sam.example/scan.pdf", None)
    storage = _storage(tmp_path)
    ocr = FakeOCR(texts=["OCR one " * 600, "OCR two " * 600])
    async with database.session(None) as session:
        doc = await session.get(OpportunityDocument, doc_id)
        assert doc is not None
        first = await parse_and_store(
            session, doc, (FIXTURES / "scanned.pdf").read_bytes(), storage=storage, ocr=ocr
        )
        assert first is not None and first.ocr_pages == 2
        assert doc.ocr_pages == 2 and doc.pages == 2
    async with database.session(None) as session:
        count_before = (
            (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(count_before) >= 2
        doc = await session.get(OpportunityDocument, doc_id)
        assert doc is not None
        again = await parse_and_store(
            session, doc, (FIXTURES / "sample.docx").read_bytes(), storage=storage
        )
        assert again is not None and again.kind == "docx"
    async with database.session(None) as session:
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
            )
            .scalars()
            .all()
        )
        doc = await session.get(OpportunityDocument, doc_id)
        assert doc is not None
    assert len(chunks) == 1 and "Statement of Work" in chunks[0].text
    assert (
        doc.pages == 2
        and doc.ocr_pages == 0
        and doc.mime_type
        and doc.mime_type.endswith("document")
    )


async def test_unparseable_bytes_mark_failed(database: Database, tmp_path: Path) -> None:
    _, doc_id = await _document(database, "https://sam.example/notes.txt", "notes.txt")
    async with database.session(None) as session:
        doc = await session.get(OpportunityDocument, doc_id)
        assert doc is not None
        result = await parse_and_store(session, doc, b"plain text", storage=_storage(tmp_path))
        assert result is None
        assert doc.status == "failed" and doc.parse_error and "unsupported" in doc.parse_error
        assert doc.parsed_text_ref is None and doc.size == 10


async def test_download_document_uses_polite_client(database: Database) -> None:
    _, doc_id = await _document(database, "https://docs.example.test/files/sow.pdf", "sow.pdf")
    data = (FIXTURES / "text.pdf").read_bytes()
    archiver = MemoryArchiver()
    client = PoliteClient(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        archiver=archiver,
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        max_attempts=1,
    )
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://docs.example.test/robots.txt").mock(return_value=httpx.Response(404))
        mock.get("https://docs.example.test/files/sow.pdf").mock(
            return_value=httpx.Response(
                200, content=data, headers={"content-type": "application/pdf"}
            )
        )
        async with database.session(None) as session:
            doc = (
                await session.execute(
                    select(OpportunityDocument)
                    .options(selectinload(OpportunityDocument.opportunity))
                    .where(OpportunityDocument.id == doc_id)
                )
            ).scalar_one()
            got = download_document(doc, client)
            assert got == data and doc.mime_type == "application/pdf"
    assert any(key.startswith("raw/sam_opps/") for key in archiver.objects)
