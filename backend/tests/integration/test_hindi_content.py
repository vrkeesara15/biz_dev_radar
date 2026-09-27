"""M3-07: IN documents are OCR'd eng+hin, a Devanagari PDF stores and chunks, and an
IN profile asking for 'hi' gets a Hindi summary alongside the English one."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.summarize import LANGUAGE_INSTRUCTIONS
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType, OpportunityIn
from app.models import CompanyProfile, DocumentChunk, Opportunity, OpportunityDocument
from app.services.documents import load_parsed_text, parse_and_store
from app.services.enrichment import HINDI, I18N_KEY, SummaryEnricher
from app.services.events import EventBus
from app.services.ingest import ingest
from app.services.storage import LocalStorage, StorageRouter
from sqlalchemy import select

from tests.factories import make_tenant
from tests.llm_fake import FakeLLM
from tests.ocr_fake import FakeOCR

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
EN = ["Scope.", "Buyer.", "Eligibility.", "Money.", "Dates."]
HI = [
    "कार्य: वार्ड 12 में सड़क निर्माण।",
    "क्रेता: लोक निर्माण विभाग, उत्तर प्रदेश।",
    "पात्रता: औसत कारोबार रु. 1.35 करोड़।",
    "राशि: अनुमानित लागत रु. 45,00,000।",
    "तिथियाँ: अंतिम तिथि 14-10-2026।",
]


def _opp(**overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": "gepnic_up",
        "external_id": "2026_PWD_770011_1",
        "region": Region.IN,
        "country": "IN",
        "currency": "INR",
        "notice_type": NoticeType.RFP,
        "title": "वार्ड संख्या 12 में सड़क निर्माण एवं मरम्मत कार्य",
        "buyer_org": "लोक निर्माण विभाग",
        "solicitation_number": "निविदा सं. 12/2026-27",
    }
    values.update(overrides)
    return OpportunityIn(**values)


async def _internal_tenant(database: Database) -> uuid.UUID:
    async with database.owner_session() as session:
        tenant = make_tenant(slug="internal", is_internal=True)
        session.add(tenant)
        await session.flush()
        return tenant.id


async def _profile(database: Database, *, region: Region, languages: list[str]) -> uuid.UUID:
    async with database.owner_session() as session:
        tenant = make_tenant(slug=f"t-{uuid.uuid4().hex[:8]}")
        session.add(tenant)
        await session.flush()
        profile = CompanyProfile(
            tenant_id=tenant.id,
            region=region,
            legal_name="Bharat Infra Works Pvt Ltd",
            output_languages=languages,
        )
        session.add(profile)
        await session.flush()
        return profile.id


# --- documents ------------------------------------------------------------------------


async def test_a_devanagari_pdf_stores_text_and_chunks(database: Database, tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path, "bidradar-in", signing_secret="s")
    async with database.session(None) as session:
        row = (await ingest(session, _opp(), bus=EventBus(), now=NOW)).opportunity
        doc = OpportunityDocument(
            opportunity_id=row.id,
            url="https://etender.up.nic.in/doc/1.pdf",
            file_name="hindi_tender.pdf",
        )
        session.add(doc)
        await session.flush()
        parsed = await parse_and_store(
            session,
            doc,
            (FIXTURES / "hindi_tender.pdf").read_bytes(),
            storage=storage,
            region=row.region,
        )
        assert parsed is not None and parsed.page_count == 3
        doc_id, opp_id = doc.id, row.id

    async with database.session(None) as session:
        doc = await session.get(OpportunityDocument, doc_id)
        assert doc is not None and doc.status == "parsed" and doc.pages == 3
        assert doc.hash and len(doc.hash) == 64
        pages = await load_parsed_text(storage, doc)
        assert len(pages) == 3 and "निविदा सूचना संख्या 12/2026-27" in pages[0]
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == doc_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        assert chunks and "पात्रता शर्तें" in "".join(c.text for c in chunks)
    row = await _row(database, opp_id)
    assert row.title.startswith("वार्ड संख्या 12")
    assert row.reference_norm == "12202627"


async def test_an_in_document_is_ocred_with_hindi_and_english(
    database: Database, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path, "bidradar-in", signing_secret="s")
    ocr = FakeOCR(texts=["स्कैन किया गया पृष्ठ " * 5])
    async with database.session(None) as session:
        row = (await ingest(session, _opp(), bus=EventBus(), now=NOW)).opportunity
        doc = OpportunityDocument(
            opportunity_id=row.id, url="https://etender.up.nic.in/scan.pdf", file_name="scan.pdf"
        )
        session.add(doc)
        await session.flush()
        parsed = await parse_and_store(
            session,
            doc,
            (FIXTURES / "scanned.pdf").read_bytes(),
            storage=storage,
            ocr=ocr,
            region=row.region,
            settings=SETTINGS,
        )
    assert parsed is not None and parsed.ocr_pages > 0
    assert ocr.calls and {languages for _size, languages in ocr.calls} == {"eng+hin"}
    assert "स्कैन किया गया" in parsed.pages[0].text


async def test_a_us_document_keeps_the_default_ocr_language_set(
    database: Database, tmp_path: Path
) -> None:
    storage = LocalStorage(tmp_path, "bidradar-us", signing_secret="s")
    ocr = FakeOCR()
    settings = Settings(_env_file=None, ocr_languages="eng")  # type: ignore[call-arg]
    async with database.session(None) as session:
        row = (
            await ingest(
                session,
                OpportunityIn(
                    source_id="sam_opps",
                    external_id="us-1",
                    region=Region.US,
                    country="US",
                    currency="USD",
                    notice_type=NoticeType.RFP,
                    title="Scanned solicitation",
                ),
                bus=EventBus(),
                now=NOW,
            )
        ).opportunity
        doc = OpportunityDocument(
            opportunity_id=row.id, url="https://sam.example/scan.pdf", file_name="scan.pdf"
        )
        session.add(doc)
        await session.flush()
        await parse_and_store(
            session,
            doc,
            (FIXTURES / "scanned.pdf").read_bytes(),
            storage=storage,
            ocr=ocr,
            region=row.region,
            settings=settings,
        )
    assert {languages for _size, languages in ocr.calls} == {"eng"}


# --- Hindi summary ----------------------------------------------------------------------


def _setup(database: Database, fake_llm: FakeLLM, tmp_path: Path) -> tuple[EventBus, Any]:
    storage = LocalStorage(tmp_path, "bidradar-in", signing_secret="s")
    router = StorageRouter(SETTINGS, overrides={Region.IN: storage})
    bus = EventBus()
    enricher = SummaryEnricher(
        llm=fake_llm, database=database, storage=router, settings=SETTINGS
    ).subscribe(bus)
    return bus, enricher


async def _row(database: Database, opp_id: uuid.UUID) -> Opportunity:
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        return row


async def test_hindi_summary_is_generated_when_an_in_profile_asks_for_it(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    await _profile(database, region=Region.IN, languages=["en", "hi"])
    bus, enricher = _setup(database, fake_llm, tmp_path)
    fake_llm.queue({"lines": EN}, {"lines": HI})
    async with database.session(None) as session:
        created = await ingest(session, _opp(), bus=bus, now=NOW)
        opp_id = created.opportunity.id

    row = await _row(database, opp_id)
    assert row.summary_ai == "\n".join(EN) and row.summary_version == 1
    assert row.extra[I18N_KEY][HINDI]["text"] == "\n".join(HI)
    assert row.extra[I18N_KEY][HINDI]["version"] == 1
    assert len(fake_llm.calls) == 2
    english, hindi = fake_llm.calls
    assert LANGUAGE_INSTRUCTIONS[HINDI] not in english.system
    assert LANGUAGE_INSTRUCTIONS[HINDI] in hindi.system
    # the untrusted content block is byte-identical, so the cached prefix is shared
    assert english.cache_blocks[0].text == hindi.cache_blocks[0].text
    assert enricher.summarised == [(opp_id, 1), (opp_id, 1)]

    async with database.session(None) as session:  # cached for the same version
        assert await enricher.summarize(session, opp_id, language=HINDI) == "\n".join(HI)
    assert len(fake_llm.calls) == 2


async def test_no_hindi_summary_without_a_profile_asking_for_it(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    await _profile(database, region=Region.IN, languages=["en"])
    await _profile(database, region=Region.US, languages=["en"])
    bus, _enricher = _setup(database, fake_llm, tmp_path)
    fake_llm.queue({"lines": EN})
    async with database.session(None) as session:
        created = await ingest(session, _opp(), bus=bus, now=NOW)
    row = await _row(database, created.opportunity.id)
    assert row.summary_ai == "\n".join(EN)
    assert I18N_KEY not in row.extra
    assert len(fake_llm.calls) == 1


async def test_a_us_notice_never_gets_a_hindi_summary(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    await _profile(database, region=Region.IN, languages=["en", "hi"])
    bus, _enricher = _setup(database, fake_llm, tmp_path)
    fake_llm.queue({"lines": EN})
    async with database.session(None) as session:
        created = await ingest(
            session,
            OpportunityIn(
                source_id="sam_opps",
                external_id="us-2",
                region=Region.US,
                country="US",
                currency="USD",
                notice_type=NoticeType.RFP,
                title="Cloud Migration",
            ),
            bus=bus,
            now=NOW,
        )
    row = await _row(database, created.opportunity.id)
    assert I18N_KEY not in row.extra and len(fake_llm.calls) == 1


async def test_the_hindi_summary_survives_a_re_ingest_and_is_redone_on_an_amendment(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    await _internal_tenant(database)
    await _profile(database, region=Region.IN, languages=["hi"])
    bus, _enricher = _setup(database, fake_llm, tmp_path)
    fake_llm.queue(
        {"lines": EN}, {"lines": HI}, {"lines": EN}, {"lines": [line.upper() for line in HI]}
    )
    async with database.session(None) as session:
        created = await ingest(session, _opp(), bus=bus, now=NOW)
        opp_id = created.opportunity.id
    async with database.session(None) as session:  # unchanged re-ingest: no event, no call
        assert (await ingest(session, _opp(), bus=bus, now=NOW)).unchanged
    row = await _row(database, opp_id)
    assert row.extra[I18N_KEY][HINDI]["text"] == "\n".join(HI), "re-ingest must not wipe it"
    assert len(fake_llm.calls) == 2

    async with database.session(None) as session:  # amendment -> version 2, new summaries
        amended = await ingest(session, _opp(description_text="संशोधन 1"), bus=bus, now=NOW)
        assert amended.version == 2
    row = await _row(database, opp_id)
    assert row.extra[I18N_KEY][HINDI]["version"] == 2
    assert row.extra[I18N_KEY][HINDI]["text"].startswith("कार्य".upper())
    assert len(fake_llm.calls) == 4
