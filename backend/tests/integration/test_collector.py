"""M5-03: the collector downloads, hashes, scans, parses (PDF, DOCX, scanned PDF via OCR),
skips unchanged hashes, marks infected files and lists manual-download items."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import httpx
import respx
from app.adapters import registry
from app.adapters.http import MemoryArchiver, PoliteClient
from app.agents import pipeline
from app.agents.collector import CollectorOutput
from app.agents.runner import AgentRunner
from app.agents.services import AgentServices
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.opportunity import NoticeType
from app.core.politeness import PolicyTable
from app.models import (
    AgentStep,
    CompanyProfile,
    DocumentChunk,
    Opportunity,
    OpportunityDocument,
    Pursuit,
)
from app.services.documents import load_parsed_text
from app.services.scanner import NoopScanner, ScanResult
from app.services.storage import LocalStorage, StorageRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tests.adapters.fixture_adapter import FixtureAdapter, raw_record
from tests.factories import create_tenant_with_owner
from tests.llm_fake import FakeLLM
from tests.ocr_fake import FakeOCR

FIXTURES = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
HOST = "https://docs.example.test"
PDF = (FIXTURES / "text.pdf").read_bytes()
DOCX = (FIXTURES / "sample.docx").read_bytes()
SCANNED = (FIXTURES / "scanned.pdf").read_bytes()


class InfectedDocx:
    name = "fake-clamav"

    async def scan(self, data: bytes) -> ScanResult:
        if data == DOCX:
            return ScanResult(clean=False, signature="Eicar-Test-Signature", scanner=self.name)
        return ScanResult(clean=True, scanner=self.name)


def _client() -> PoliteClient:
    return PoliteClient(
        settings=SETTINGS,
        archiver=MemoryArchiver(),
        sleep=lambda s: None,
        policies=PolicyTable(default_rate=1e6),
        max_attempts=1,
    )


def _services(tmp_path: Path, *, scanner: Any = None, ocr: FakeOCR | None = None) -> AgentServices:
    storage = LocalStorage(tmp_path, "bidradar-us", signing_secret="s")
    return AgentServices(
        settings=SETTINGS,
        storage=StorageRouter(SETTINGS, overrides={Region.US: storage}),
        scanner=scanner or NoopScanner(),
        ocr=ocr,
        http_factory=_client,
    )


async def _setup(
    database: Database, urls: list[tuple[str, str | None]], **opp_overrides: Any
) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        profile = CompanyProfile(tenant_id=tenant.id, region=Region.US, legal_name="Collect LLC")
        values: dict[str, Any] = {
            "source_id": "fixture",
            "external_id": f"col-{uuid.uuid4().hex[:6]}",
            "region": Region.US,
            "country": "US",
            "currency": "USD",
            "notice_type": NoticeType.RFP,
            "title": "Collector notice",
            "source_url": "https://portal.example.test/notice/1",
        }
        values.update(opp_overrides)
        opp = Opportunity(**values)
        session.add_all([profile, opp])
        await session.flush()
        for url, name in urls:
            session.add(OpportunityDocument(opportunity_id=opp.id, url=url, file_name=name))
        pursuit = Pursuit(
            tenant_id=tenant.id, profile_id=profile.id, opportunity_id=opp.id, created_by=user.id
        )
        session.add(pursuit)
        await session.flush()
        return {"tenant_id": tenant.id, "opportunity_id": opp.id, "pursuit_id": pursuit.id}


async def _run(database: Database, ctx: dict[str, Any], services: AgentServices) -> Any:
    specs, finish = pipeline.plan_steps("collect")
    assert [s.agent for s in specs] == ["collect"] and finish.status == "done"
    runner = AgentRunner(database, tenant_id=ctx["tenant_id"], llm=FakeLLM(), services=services)
    run_id = await runner.start(
        kind="pipeline", pursuit_id=ctx["pursuit_id"], params={"step": "collect"}
    )
    result = await runner.run(run_id, specs)
    return run_id, result


async def _docs(database: Database, opportunity_id: uuid.UUID) -> list[OpportunityDocument]:
    async with database.session(None) as session:
        rows = (
            (
                await session.execute(
                    select(OpportunityDocument)
                    .options(selectinload(OpportunityDocument.chunks))
                    .where(OpportunityDocument.opportunity_id == opportunity_id)
                    .order_by(OpportunityDocument.url)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


def _mock_files(mock: respx.MockRouter, *, docx: bytes = DOCX) -> None:
    mock.get(f"{HOST}/robots.txt").mock(return_value=httpx.Response(404))
    mock.get(f"{HOST}/a-sow.pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    )
    mock.get(f"{HOST}/b-terms.docx").mock(return_value=httpx.Response(200, content=docx))
    mock.get(f"{HOST}/c-scan.pdf").mock(
        return_value=httpx.Response(
            200, content=SCANNED, headers={"content-type": "application/pdf"}
        )
    )


URLS = [
    (f"{HOST}/a-sow.pdf", "sow.pdf"),
    (f"{HOST}/b-terms.docx", "terms.docx"),
    (f"{HOST}/c-scan.pdf", None),
]


async def test_collects_pdf_docx_and_scanned_pdf_then_skips_unchanged(
    database: Database, tmp_path: Path
) -> None:
    ctx = await _setup(database, URLS)
    ocr = FakeOCR(texts=["OCR one " * 300, "OCR two " * 300])
    services = _services(tmp_path, ocr=ocr)
    with respx.mock(assert_all_called=True) as mock:
        _mock_files(mock)
        run_id, result = await _run(database, ctx, services)
    assert result.status == "done", result.error
    out = CollectorOutput.model_validate(result.outputs["collect"])
    assert out.opportunity_id == ctx["opportunity_id"] and out.manual_items == []
    by_url = {d.url: d for d in out.documents}
    assert set(by_url) == {u for u, _ in URLS}
    pdf, docx, scan = (by_url[u] for u, _ in URLS)
    assert (pdf.status, pdf.pages, pdf.ocr_pages) == ("parsed", 3, 0) and pdf.sections_count == 3
    assert pdf.sha256 == hashlib.sha256(PDF).hexdigest() and pdf.file_name == "sow.pdf"
    assert (docx.status, docx.pages) == ("parsed", 2) and docx.sections_count >= 3
    assert (scan.status, scan.pages, scan.ocr_pages) == ("parsed", 2, 2)
    assert len(ocr.calls) == 2 and all(lang == "eng+hin" for _, lang in ocr.calls)
    assert out.tasks == [] and out.parsed_count == 3

    rows = await _docs(database, ctx["opportunity_id"])
    assert [r.status for r in rows] == ["parsed", "parsed", "parsed"]
    assert all(len(r.hash or "") == 64 and r.parsed_text_ref and r.chunks for r in rows)
    pages = await load_parsed_text(services.storage_for(Region.US), rows[0])
    assert len(pages) == 3 and "FedRAMP Moderate" in pages[1]
    scanned_pages = await load_parsed_text(services.storage_for(Region.US), rows[2])
    assert len(scanned_pages) == 2 and all("OCR" in page for page in scanned_pages)

    # the output is versioned on the step row
    async with database.session(ctx["tenant_id"]) as session:
        step = (
            await session.execute(select(AgentStep).where(AgentStep.run_id == run_id))
        ).scalar_one()
        assert step.agent == "collect" and step.status == "done" and step.tokens_in == 0
        assert step.input_ref == f"opportunity:{ctx['opportunity_id']}@1"
        assert step.output["documents"][0]["status"] == "parsed"

    # second run: same bytes -> unchanged, no re-parse (no new OCR calls, chunks untouched)
    chunk_ids = {c.id for r in rows for c in r.chunks}
    with respx.mock(assert_all_called=True) as mock:
        _mock_files(mock)
        _, again = await _run(database, ctx, services)
    out2 = CollectorOutput.model_validate(again.outputs["collect"])
    assert [d.status for d in out2.documents] == ["unchanged"] * 3 and len(ocr.calls) == 2
    rows = await _docs(database, ctx["opportunity_id"])
    assert {c.id for r in rows for c in r.chunks} == chunk_ids

    # a changed document is re-parsed, the others stay unchanged
    with respx.mock(assert_all_called=True) as mock:
        _mock_files(mock)
        mock.get(f"{HOST}/b-terms.docx").mock(return_value=httpx.Response(200, content=PDF))
        _, third = await _run(database, ctx, services)
    out3 = {
        d.url: d.status for d in CollectorOutput.model_validate(third.outputs["collect"]).documents
    }
    assert out3 == {URLS[0][0]: "unchanged", URLS[1][0]: "parsed", URLS[2][0]: "unchanged"}
    rows = await _docs(database, ctx["opportunity_id"])
    assert rows[1].hash == hashlib.sha256(PDF).hexdigest() and rows[1].pages == 3


async def test_infected_file_is_marked_and_becomes_a_task(
    database: Database, tmp_path: Path
) -> None:
    ctx = await _setup(database, URLS[:2])
    services = _services(tmp_path, scanner=InfectedDocx())
    with respx.mock(assert_all_called=False) as mock:
        _mock_files(mock)
        _, result = await _run(database, ctx, services)
    assert result.status == "done"
    out = CollectorOutput.model_validate(result.outputs["collect"])
    statuses = {d.url: d for d in out.documents}
    assert statuses[URLS[0][0]].status == "parsed"
    bad = statuses[URLS[1][0]]
    assert bad.status == "infected" and bad.error == "infected: Eicar-Test-Signature"
    assert len(out.tasks) == 1 and "Eicar-Test-Signature" in out.tasks[0].detail
    assert out.tasks[0].document_id == bad.document_id and "terms.docx" in out.tasks[0].title
    rows = await _docs(database, ctx["opportunity_id"])
    infected = next(r for r in rows if r.url == URLS[1][0])
    assert infected.status == "infected" and infected.parsed_text_ref is None
    assert infected.hash == hashlib.sha256(DOCX).hexdigest() and infected.chunks == []
    async with database.session(None) as session:
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == infected.id)
                )
            )
            .scalars()
            .all()
        )
    assert chunks == []


async def test_manual_sources_and_forbidden_downloads_become_manual_items(
    database: Database, tmp_path: Path
) -> None:
    ctx = await _setup(
        database,
        [(f"{HOST}/a-sow.pdf", "sow.pdf"), (f"{HOST}/gated.pdf", "gated.pdf")],
        detail_status="manual",
    )
    with respx.mock(assert_all_called=False) as mock:
        _mock_files(mock)
        mock.get(f"{HOST}/gated.pdf").mock(return_value=httpx.Response(403))
        _, result = await _run(database, ctx, _services(tmp_path))
    out = CollectorOutput.model_validate(result.outputs["collect"])
    assert [m.url for m in out.manual_items] == [
        "https://portal.example.test/notice/1",
        f"{HOST}/gated.pdf",
    ]
    assert "CAPTCHA" in out.manual_items[0].reason and "HTTP 403" in out.manual_items[1].reason
    by_url = {d.url: d.status for d in out.documents}
    assert by_url == {f"{HOST}/a-sow.pdf": "parsed", f"{HOST}/gated.pdf": "manual"}
    rows = await _docs(database, ctx["opportunity_id"])
    gated = next(r for r in rows if r.url.endswith("gated.pdf"))
    assert gated.status == "failed" and gated.parse_error and "403" in gated.parse_error


class DocsFixtureAdapter(FixtureAdapter):
    """fetch_detail/fetch_documents answer for any external id with two attachments."""

    def __init__(self) -> None:
        super().__init__([raw_record("any", docs=[f"{HOST}/a-sow.pdf", f"{HOST}/c-scan.pdf"])])

    def fetch_detail(self, external_id: str) -> Any:
        return self.records[0]


async def test_missing_documents_are_discovered_through_the_adapter(
    database: Database, tmp_path: Path
) -> None:
    ctx = await _setup(database, [])
    with registry.temporarily(DocsFixtureAdapter), respx.mock(assert_all_called=False) as mock:
        _mock_files(mock)
        _, result = await _run(database, ctx, _services(tmp_path, ocr=FakeOCR()))
    out = CollectorOutput.model_validate(result.outputs["collect"])
    assert sorted(d.url for d in out.documents) == [f"{HOST}/a-sow.pdf", f"{HOST}/c-scan.pdf"]
    assert {d.status for d in out.documents} == {"parsed"} and out.warnings == []
    rows = await _docs(database, ctx["opportunity_id"])
    assert [r.file_name for r in rows] == ["a-sow.pdf", "c-scan.pdf"]
    # without an adapter the step still succeeds and says why nothing was found
    ctx2 = await _setup(database, [], source_id="nope")
    _, result2 = await _run(database, ctx2, _services(tmp_path))
    out2 = CollectorOutput.model_validate(result2.outputs["collect"])
    assert out2.documents == [] and out2.warnings == ["no adapter registered for source 'nope'"]
