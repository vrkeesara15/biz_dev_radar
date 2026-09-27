"""M7-11: the automated half of the SPEC 12 "India testing checklist (staging-in)".

One test per checklist row, named after the row, so `docs/runbooks/india-testing.md` can
cite a test id next to every item it claims is automated. The manual rows (WhatsApp
template approval and real delivery, SES Mumbai deliverability, a Razorpay test payment,
Indian-ISP latency, the two pilot end-to-end bids) are in the runbook, not here — what is
automated here is the code those manual steps exercise, so a regression is caught before
anybody spends a staging-in afternoon on it.

Nothing in this file touches the network: the LLM is the FakeLLM, the BSP and Razorpay
are respx mocks, storage is the local backend, and the Terraform tree is read as text.

Run it on its own with `make india-check` (or, until that target lands,
`cd backend && uv run pytest tests/integration/test_india_checklist.py`).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import hcl2
import httpx
import pytest
import respx
from app.core.billing import (
    BillingEventKind,
    BillingProviderName,
    gst_breakdown,
    parse_razorpay_event,
    provider_for_region,
)
from app.core.compliance import ChecklistContext, build_checklist
from app.core.config import Region, Settings
from app.core.dates import IST, dual_tz, parse_in
from app.core.db import Database
from app.core.display_time import tz_fields
from app.core.eligibility_in import (
    MSE_EXEMPTION,
    STARTUP_EXEMPTION,
    CriteriaIn,
    ProfileSnapshotIn,
    Status,
    evaluate_in,
)
from app.core.finance import FiscalYearRevenue
from app.core.money import format_inr, parse_inr
from app.core.opportunity import DocumentRef, NoticeType, OpportunityIn
from app.core.plan import Plan
from app.models import DocumentChunk, Opportunity, OpportunityDocument
from app.notify.email import SESProvider, build_email_provider, ses_region_for
from app.notify.whatsapp import WhatsAppTarget, build_provider, template_for
from app.services.billing.razorpay import RazorpayProvider
from app.services.dedupe import find_duplicates
from app.services.documents import load_parsed_text, parse_and_store
from app.services.events import EventBus
from app.services.gem_extraction import EXTRA_KEY, STATUS_OK, GemBidExtractor
from app.services.ingest import ingest
from app.services.storage import LocalStorage, StorageRouter, bucket_for_region
from sqlalchemy import select

from tests.factories import make_tenant
from tests.llm_fake import FakeLLM

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "evals" / "golden" / "in" / "gem"
DOCUMENTS = Path(__file__).resolve().parents[1] / "adapters" / "fixtures" / "documents"
BID = "GEM-2026-B-1234567"
GEM_ANSWER: dict[str, Any] = json.loads((GOLDEN / f"{BID}.llm.json").read_text())
GEM_EXPECTED: dict[str, Any] = json.loads((GOLDEN / f"{BID}.expected.json").read_text())

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
RAZORPAY_API = "https://api.razorpay.com/v1"
SUPPLIER_GSTIN = "29AAACB1234C1ZP"  # Karnataka (state code 29)
CUSTOMER_GSTIN = "27AABCU9603R1ZM"  # Maharashtra (state code 27)


# --- helpers ------------------------------------------------------------------------------


def _in_opp(source_id: str, external_id: str, **overrides: Any) -> OpportunityIn:
    values: dict[str, Any] = {
        "source_id": source_id,
        "external_id": external_id,
        "source_url": f"https://{source_id}.example.test/{external_id}",
        "region": Region.IN,
        "country": "IN",
        "currency": "INR",
        "notice_type": NoticeType.RFP,
        "title": "Supply and installation of solar street lights",
        "buyer_org": "Government of Tamil Nadu",
        "solicitation_number": "TN/MAWS/2026/4471",
        "source_tz": IST,
        "posted_at": NOW - timedelta(days=1),
        "response_due_at": DUE,
    }
    values.update(overrides)
    return OpportunityIn(**values)


async def _ingest(database: Database, opp: OpportunityIn, bus: EventBus | None = None) -> Any:
    async with database.session(None) as session:
        return await ingest(session, opp, bus=bus or EventBus(), now=NOW)


async def _row(database: Database, opp_id: uuid.UUID) -> Opportunity:
    async with database.session(None) as session:
        row = await session.get(Opportunity, opp_id)
        assert row is not None
        return row


async def _internal_tenant(database: Database) -> uuid.UUID:
    async with database.owner_session() as session:
        tenant = make_tenant(slug="internal", is_internal=True)
        session.add(tenant)
        await session.flush()
        return tenant.id


def _profile(**overrides: Any) -> ProfileSnapshotIn:
    """An Indian bidder too young and too small for the tender below."""
    values: dict[str, Any] = {
        "revenue": (
            FiscalYearRevenue(fiscal_year=2023, amount=Decimal("2000000"), currency="INR"),
            FiscalYearRevenue(fiscal_year=2024, amount=Decimal("2500000"), currency="INR"),
            FiscalYearRevenue(fiscal_year=2025, amount=Decimal("3000000"), currency="INR"),
        ),
        "year_founded": 2024,
    }
    values.update(overrides)
    return ProfileSnapshotIn(**values)


TENDER = CriteriaIn(
    min_avg_turnover_inr=Decimal("45000000"),  # ₹4.5 Cr
    min_experience_years=5,
    emd_amount_inr=Decimal("240000"),
)
TODAY = date(2026, 9, 26)


# --- Dates: DD-MM-YYYY and "17-Jul-2026 08:23 PM" parse; IST shown --------------------------


def test_dates_both_portal_formats_parse_as_ist() -> None:
    """Checklist: 'DD-MM-YYYY and "17-Jul-2026 08:23 PM" formats parse correctly'."""
    numeric = parse_in("14-10-2026")
    assert numeric is not None
    assert numeric.source_tz == IST and numeric.has_time is False
    # a bare date is local midnight IST = 18:30 UTC the day before
    assert numeric.utc == datetime(2026, 10, 13, 18, 30, tzinfo=UTC)
    assert numeric.local.date() == date(2026, 10, 14)

    gem = parse_in("17-Jul-2026 08:23 PM")
    assert gem is not None
    assert gem.has_time is True and gem.source_tz == IST
    assert gem.utc == datetime(2026, 7, 17, 14, 53, tzinfo=UTC)
    assert gem.local.strftime("%d-%b-%Y %I:%M %p") == "17-Jul-2026 08:23 PM"

    # the same instant however the portal punctuates it
    for text in ("17/07/2026 20:23", "17 July 2026 8:23 PM", "17-07-2026 20:23:00 Hrs (IST)"):
        parsed = parse_in(text)
        assert parsed is not None and parsed.utc == gem.utc, text

    assert parse_in("not a date") is None
    assert parse_in("31-02-2026") is None  # a date that does not exist, not an exception


def test_dates_an_indian_deadline_is_shown_in_ist() -> None:
    """Checklist: 'IST shown'."""
    parsed = parse_in("17-Jul-2026 08:23 PM")
    assert parsed is not None
    out = tz_fields(parsed.utc, IST, user_tz=IST)
    assert out.buyer_tz == IST
    assert out.buyer_display == "Jul 17, 8:23 PM IST"
    # buyer and user are the same zone: the reader gets one unambiguous rendering
    assert out.display == "Jul 17, 8:23 PM IST"
    assert out.buyer_local == "2026-07-17T20:23:00+05:30"


def test_dates_an_overnight_us_deadline_is_shown_in_ist() -> None:
    """Checklist: 'overnight US deadlines shown in IST'."""
    # 5 PM Eastern on Oct 14 is 2:30 AM IST on Oct 15 — the Indian reader must see the
    # date roll over, or they lose a day.
    due = datetime(2026, 10, 14, 21, 0, tzinfo=UTC)
    assert dual_tz(due, "America/New_York", IST) == "Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST"

    out = tz_fields(due, "America/New_York", user_tz=IST)
    assert out.user_display == "Oct 15, 2:30 AM IST"
    assert out.user_local == "2026-10-15T02:30:00+05:30"
    assert out.buyer_local == "2026-10-14T17:00:00-04:00"


# --- Money: lakh/crore formatting, EMD and tender fee ---------------------------------------


def test_money_inr_lakh_and_crore_formatting_round_trips() -> None:
    """Checklist: 'INR with lakh/crore formatting (₹12,50,000; ₹1.2 Cr)'."""
    assert format_inr(Decimal("1250000")) == "₹12,50,000"
    assert format_inr(Decimal("12000000"), compact=True) == "₹1.2 Cr"
    assert format_inr(Decimal("1250000"), compact=True) == "₹12.5 Lakh"
    assert format_inr(Decimal("99999")) == "₹99,999"  # below a lakh stays in full

    # every rendering the portals and our own UI produce parses back to the same number
    assert parse_inr("₹12,50,000") == Decimal("1250000")
    assert parse_inr(format_inr(Decimal("12000000"), compact=True)) == Decimal("12000000")
    assert parse_inr("Rs. 1.2 Cr") == Decimal("12000000")
    assert parse_inr("45 Lakh (s)") == Decimal("4500000")
    assert parse_inr("₹25,000/- (Rupees Twenty Five Thousand only)") == Decimal("25000")
    assert parse_inr("$25,000") is None  # a dollar figure is never read as rupees


async def test_money_emd_is_extracted_from_the_gem_bid_pdf_onto_the_row(
    database: Database, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Checklist: 'EMD ... extracted' — replayed from the golden GeM fixture, no network."""
    await _internal_tenant(database)
    storage = LocalStorage(tmp_path, "bidradar-in", signing_secret="s")
    router = StorageRouter(SETTINGS, overrides={Region.IN: storage})
    bus = EventBus()
    extractor = GemBidExtractor(
        llm=fake_llm, database=database, storage=router, settings=SETTINGS
    ).subscribe(bus)
    fake_llm.queue(GEM_ANSWER)

    async with database.session(None) as session:
        created = await ingest(
            session,
            _in_opp(
                "gem",
                "GEM/2026/B/1234567",
                notice_type=NoticeType.GEM_BID,
                title="Desktop Computers x 120 (GEM/2026/B/1234567)",
                solicitation_number="GEM/2026/B/1234567",
                buyer_org="Ministry of Railways",
                eligibility={"requires_gem_registration": True},
                documents=[
                    DocumentRef(
                        url="https://bidplus.gem.gov.in/showbidDocument/7891234",
                        file_name=f"{BID}.pdf",
                        mime_type="application/pdf",
                    )
                ],
            ),
            bus=EventBus(),
            now=NOW,
        )
        opp_id = created.opportunity.id
        doc = (
            await session.execute(
                select(OpportunityDocument).where(OpportunityDocument.opportunity_id == opp_id)
            )
        ).scalar_one()
        await parse_and_store(session, doc, (GOLDEN / f"{BID}.pdf").read_bytes(), storage=storage)
    async with database.session(None) as session:
        assert await extractor.extract(session, opp_id) is not None

    row = await _row(database, opp_id)
    assert row.extra[EXTRA_KEY]["status"] == STATUS_OK
    # "2,40,000" in the document -> the canonical numeric column, and the jsonb criteria
    assert row.emd_amount == Decimal(GEM_EXPECTED["emd_amount_inr"]) == Decimal("240000")
    assert row.eligibility["emd_amount_inr"] == GEM_EXPECTED["emd_amount_inr"]
    assert format_inr(row.emd_amount) == "₹2,40,000"
    # "Rs. 1.20 Crore" -> ₹1.2 Cr on the value columns
    assert row.estimated_value_max == Decimal("12000000")
    assert format_inr(row.estimated_value_max, compact=True) == "₹1.2 Cr"
    assert CriteriaIn.from_dict(row.eligibility).emd_amount_inr == Decimal("240000")


async def test_money_tender_fee_is_stored_and_reaches_the_submission_checklist(
    database: Database,
) -> None:
    """Checklist: '... and tender fee extracted'.

    `opportunities.tender_fee` is the column, filled from the notice through
    `OpportunityIn.tender_fee`; `core.money.parse_inr` reads the portal's wording and the
    SPEC 8 checklist turns the stored figure into a required payment item. No Indian
    adapter fills the column from a live page yet (OQ-138) — that is a manual row.
    """
    fee = parse_inr("Tender Document Fee: Rs. 1,180/- (non-refundable)")
    assert fee == Decimal("1180")
    emd = parse_inr("EMD: ₹2,40,000/-")
    assert emd == Decimal("240000")

    created = await _ingest(
        database, _in_opp("gepnic_tn", "2026_TNMAWS_4471_1", tender_fee=fee, emd_amount=emd)
    )
    row = await _row(database, created.opportunity.id)
    assert row.tender_fee == Decimal("1180") and row.emd_amount == Decimal("240000")

    items = {
        item.key: item
        for item in build_checklist(
            ChecklistContext(
                region="in",
                notice_type="rfp",
                source_id="gepnic_tn",
                currency="INR",
                emd_amount=row.emd_amount,
                tender_fee=row.tender_fee,
            ),
            [],
        )
    }
    assert items["tender_fee"].required is True
    assert items["tender_fee"].note == "Tender fee stated on the notice: ₹1,180."
    assert items["emd"].required is True
    assert items["emd"].note == "EMD stated on the notice: ₹2,40,000."


# --- Content: Hindi/bilingual titles and PDFs; transliterated dedupe ------------------------


async def test_content_hindi_title_and_pdf_parse_and_chunk_with_devanagari_intact(
    database: Database, tmp_path: Path
) -> None:
    """Checklist: 'Hindi/bilingual tender titles and PDFs (OCR hin) do not break parsing'."""
    storage = LocalStorage(tmp_path, "bidradar-in", signing_secret="s")
    title = "वार्ड संख्या 12 में सड़क निर्माण एवं मरम्मत कार्य / Road works in Ward 12"
    async with database.session(None) as session:
        created = await ingest(
            session,
            _in_opp(
                "gepnic_up",
                "2026_PWD_770011_1",
                title=title,
                buyer_org="लोक निर्माण विभाग",
                solicitation_number="निविदा सं. 12/2026-27",
            ),
            bus=EventBus(),
            now=NOW,
        )
        opp_id = created.opportunity.id
        doc = OpportunityDocument(
            opportunity_id=opp_id,
            url="https://etender.up.nic.in/doc/1.pdf",
            file_name="hindi_tender.pdf",
        )
        session.add(doc)
        await session.flush()
        parsed = await parse_and_store(
            session,
            doc,
            (DOCUMENTS / "hindi_tender.pdf").read_bytes(),
            storage=storage,
            region=Region.IN,
        )
        assert parsed is not None and parsed.page_count == 3
        doc_id = doc.id

    async with database.session(None) as session:
        stored = await session.get(OpportunityDocument, doc_id)
        assert stored is not None and stored.status == "parsed"
        pages = await load_parsed_text(storage, stored)
        assert "निविदा सूचना संख्या 12/2026-27" in pages[0]
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
    assert row.title == title  # the bilingual title survives byte for byte
    assert row.buyer_org == "लोक निर्माण विभाग"
    # the Devanagari reference normalises to its digits so it can still key a dedupe
    assert row.reference_norm == "12202627"
    # OCR language set for an Indian notice is eng+hin (SPEC 12 "OCR hin")
    assert SETTINGS.ocr_languages_in == "eng+hin"


async def test_content_transliterated_organisation_names_dedupe_a_cppp_mirror(
    database: Database,
) -> None:
    """Checklist: 'transliterated organisation names dedupe'."""
    mirror = await _ingest(
        database,
        _in_opp(
            "cppp",
            "2026_TNMAWS_4471_1",
            buyer_org="Govt. of Tamil Nadu",
            source_url="https://eprocure.gov.in/cppp/tendersfullview/abc",
        ),
    )
    state = await _ingest(
        database,
        _in_opp(
            "gepnic_tn",
            "2026_TNMAWS_4471_1",
            buyer_org="Government of Tamil Nadu",
            source_url="https://tntenders.gov.in/nicgep/app?component=view",
            buyer_office="Coimbatore City Municipal Corporation",
            emd_amount=Decimal("25000"),
            tender_fee=Decimal("1180"),
        ),
    )
    assert state.merged and state.merged[0].method == "cross_source_key"
    loser = await _row(database, mirror.opportunity.id)
    winner = await _row(database, state.opportunity.id)
    assert loser.buyer_norm == winner.buyer_norm == "government of tamil nadu"
    assert loser.duplicate_of == winner.id
    # the CPPP link stays reachable from the survivor (SPEC 14 attribution)
    assert winner.extra["also_from"][0]["source_id"] == "cppp"

    # the two rows are each other's candidate purely because the transliteration table
    # put "Govt. of Tamil Nadu" and "Government of Tamil Nadu" on one buyer_norm
    async with database.session(None) as session:
        candidates = await find_duplicates(session, winner)
    assert [(c.id, c.method) for c in candidates] == [(loser.id, "cross_source_key")]


# --- MSME/Udyam and Startup exemptions ------------------------------------------------------


def test_exemptions_msme_udyam_flips_the_same_criteria_from_fail_to_pass() -> None:
    """Checklist: 'MSME/Udyam ... exemptions correctly change eligibility results'."""
    profile = _profile()
    plain = evaluate_in(profile, TENDER, TODAY)
    assert plain.status is Status.FAIL
    assert {r.name for r in plain.results if r.status is Status.FAIL} == {
        "turnover",
        "experience",
    }
    assert plain.exemptions == ()

    udyam = _profile(udyam_number="UDYAM-TN-01-0001234", udyam_category="micro")
    offered = dataclasses.replace(TENDER, allows_mse_exemption=True)
    relaxed = evaluate_in(udyam, offered, TODAY)
    assert relaxed.status is Status.PASS and relaxed.score == Decimal(1)
    assert relaxed.exemptions == (MSE_EXEMPTION,)
    by_name = {r.name: r for r in relaxed.results}
    assert by_name["turnover"].exemption_applied == MSE_EXEMPTION
    assert by_name["experience"].exemption_applied == MSE_EXEMPTION
    assert "waived" in by_name["emd"].reason  # EMD waived for an MSE

    # a tender that does NOT offer the relaxation still fails, and says why
    unchanged = evaluate_in(udyam, TENDER, TODAY)
    assert unchanged.status is Status.FAIL
    assert "Udyam MSE relaxation not offered" in by_name_of(unchanged, "turnover").reason

    # Udyam "medium" is not an MSE, so the offered exemption does not apply
    medium = _profile(udyam_number="UDYAM-TN-01-0009999", udyam_category="medium")
    assert evaluate_in(medium, offered, TODAY).status is Status.FAIL


def test_exemptions_dpiit_startup_flips_the_same_criteria_from_fail_to_pass() -> None:
    """Checklist: '... and Startup exemptions correctly change eligibility results'."""
    startup = _profile(dpiit_number="DIPP12345")
    assert evaluate_in(startup, TENDER, TODAY).status is Status.FAIL

    offered = dataclasses.replace(TENDER, allows_startup_exemption=True)
    relaxed = evaluate_in(startup, offered, TODAY)
    assert relaxed.status is Status.PASS
    assert relaxed.exemptions == (STARTUP_EXEMPTION,)
    assert by_name_of(relaxed, "experience").exemption_applied == STARTUP_EXEMPTION

    # both statuses held and both offered: MSE is reported first, and it still passes
    both = _profile(
        udyam_number="UDYAM-TN-01-0001234", udyam_category="small", dpiit_number="DIPP12345"
    )
    all_offered = dataclasses.replace(
        TENDER, allows_mse_exemption=True, allows_startup_exemption=True
    )
    out = evaluate_in(both, all_offered, TODAY)
    assert out.status is Status.PASS and out.exemptions == (MSE_EXEMPTION,)


def by_name_of(out: Any, name: str) -> Any:
    return next(r for r in out.results if r.name == name)


# --- WhatsApp templates, SES Mumbai -----------------------------------------------------------


def test_notifications_ses_is_mumbai_for_an_indian_tenant() -> None:
    """Checklist: 'SES Mumbai email deliverability' — the region half, automated.

    SPF/DKIM/DMARC and an actual inbox placement test are manual rows in the runbook.
    """
    assert SETTINGS.ses_region_in == "ap-south-1"
    assert ses_region_for(SETTINGS, Region.IN) == "ap-south-1"
    assert ses_region_for(SETTINGS, "in") == "ap-south-1"
    assert ses_region_for(SETTINGS, Region.US) == SETTINGS.ses_region_us == "us-east-1"

    ses_settings = Settings(_env_file=None, email_provider="ses")  # type: ignore[call-arg]
    provider = build_email_provider(ses_settings, region=Region.IN)
    assert isinstance(provider, SESProvider) and provider.region_name == "ap-south-1"
    us_provider = build_email_provider(ses_settings, region=Region.US)
    assert isinstance(us_provider, SESProvider) and us_provider.region_name == "us-east-1"


@pytest.mark.parametrize(
    ("region", "phone", "verified", "eligible"),
    [
        ("in", "+919876543210", True, True),
        ("in", "+919876543210", False, False),  # number recorded but never verified
        ("in", None, True, False),  # no number at all
        ("us", "+12025550147", True, False),  # verified, but not an Indian tenant
    ],
)
def test_notifications_whatsapp_is_offered_only_to_a_verified_indian_user(
    region: str, phone: str | None, verified: bool, eligible: bool
) -> None:
    """Checklist: 'WhatsApp templates approved and delivered' — the gating half.

    Template approval in the BSP console and a real delivery receipt are manual rows.
    """
    target = WhatsAppTarget(phone_e164=phone, phone_verified=verified, region=region)
    assert target.eligible is eligible


def test_notifications_whatsapp_sends_only_pre_approved_templates() -> None:
    """A business-initiated WhatsApp message is only ever a pre-approved template, and the
    template names are configuration — an event with no approved name is simply not sent."""
    off = Settings(_env_file=None)  # type: ignore[call-arg]
    assert template_for(off, "deadline_reminder") is None
    assert template_for(off, "high_fit_match") is None
    assert build_provider(off) is None  # no BSP configured -> the channel never sends

    approved = Settings(  # type: ignore[call-arg]
        _env_file=None,
        whatsapp_provider="gupshup",
        whatsapp_template_deadline="bidradar_deadline_v1",
        whatsapp_template_high_match="bidradar_high_match_v1",
        gupshup_api_key="key",
        gupshup_source_number="+919999999999",
        gupshup_app_name="BidRadar",
    )
    assert template_for(approved, "deadline_reminder") == "bidradar_deadline_v1"
    assert template_for(approved, "high_fit_match") == "bidradar_high_match_v1"
    # SPEC 7 puts WhatsApp on exactly those two rows and nothing else
    assert template_for(approved, "opportunity.amended") is None
    assert build_provider(approved) is not None

    # credentials half-filled: still off, rather than failing at send time
    half = Settings(  # type: ignore[call-arg]
        _env_file=None, whatsapp_provider="gupshup", gupshup_api_key="key"
    )
    assert build_provider(half) is None


# --- Razorpay test payments with GST invoice ---------------------------------------------------


@respx.mock
async def test_payments_razorpay_is_chosen_for_in_tenants_and_carries_the_gst_fields() -> None:
    """Checklist: 'Razorpay test payments with GST invoice' — provider choice and payload.

    Running a real test-mode card through the hosted page is a manual row.
    """
    assert provider_for_region(Region.IN) is BillingProviderName.RAZORPAY
    assert provider_for_region(Region.US) is BillingProviderName.STRIPE

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        razorpay_key_id="rzp_test_123",
        razorpay_key_secret="rzp_secret",
        razorpay_webhook_secret="whsec",
        razorpay_plan_ids={"pro": "plan_pro"},
        billing_gstin=SUPPLIER_GSTIN,
        billing_gst_rate_pct=18,
    )
    provider = RazorpayProvider.from_settings(settings)
    assert provider.configured is True

    subscriptions = respx.post(f"{RAZORPAY_API}/subscriptions").mock(
        return_value=httpx.Response(
            200, json={"id": "sub_rzp", "short_url": "https://rzp.io/i/sub_rzp"}
        )
    )
    tenant_id = uuid.uuid4()
    session = await provider.create_checkout(
        customer_id="cust_rzp",
        tenant_id=tenant_id,
        plan=Plan.PRO,
        success_url="https://app.example/ok",
        cancel_url="https://app.example/no",
        gst_details={
            "gstin": CUSTOMER_GSTIN,
            "place_of_supply": "27",
            "legal_name": "Bharat Infra Pvt Ltd",
        },
    )
    assert session.url == "https://rzp.io/i/sub_rzp" and session.provider == "razorpay"
    sent = json.loads(subscriptions.calls.last.request.read())
    assert sent["plan_id"] == "plan_pro"
    assert sent["notes"]["tenant_id"] == str(tenant_id)
    assert sent["notes"]["gstin"] == CUSTOMER_GSTIN
    assert sent["notes"]["place_of_supply"] == "27"
    assert sent["notes"]["legal_name"] == "Bharat Infra Pvt Ltd"


def test_payments_a_paid_invoice_carries_a_gst_breakdown_to_inspect() -> None:
    """The invoice fields a reviewer checks on the Razorpay test invoice."""
    # inter-state: supplier in 29 (Karnataka), place of supply 27 (Maharashtra) -> IGST
    inter = gst_breakdown(118000, supplier_gstin=SUPPLIER_GSTIN, place_of_supply="27", rate_pct=18)
    assert inter["supply_type"] == "inter_state"
    assert inter["taxable_paise"] == 100000 and inter["igst_paise"] == 18000
    assert inter["cgst_paise"] == inter["sgst_paise"] == 0
    assert inter["taxable_paise"] + inter["igst_paise"] == inter["total_paise"]

    # intra-state: same state on both sides -> CGST + SGST, and the lines still add up
    intra = gst_breakdown(118000, supplier_gstin=SUPPLIER_GSTIN, place_of_supply="29", rate_pct=18)
    assert intra["supply_type"] == "intra_state"
    assert intra["cgst_paise"] == intra["sgst_paise"] == 9000 and intra["igst_paise"] == 0

    event = parse_razorpay_event(
        {
            "event": "invoice.paid",
            "payload": {
                "invoice": {
                    "entity": {
                        "id": "inv_1",
                        "amount": 118000,
                        "currency": "INR",
                        "notes": {
                            "tenant_id": str(uuid.uuid4()),
                            "plan": "pro",
                            "gstin": CUSTOMER_GSTIN,
                            "place_of_supply": "27",
                        },
                    }
                }
            },
        },
        plan_ids={"pro": "plan_pro"},
        event_id="evt_1",
        supplier_gstin=SUPPLIER_GSTIN,
        gst_rate_pct=18,
    )
    assert event is not None and event.kind is BillingEventKind.INVOICE_PAID
    assert event.gst["supplier_gstin"] == SUPPLIER_GSTIN
    assert event.gst["recipient_gstin"] == CUSTOMER_GSTIN
    assert event.gst["place_of_supply"] == "27"
    assert event.gst["tax_rate_pct"] == 18 and event.gst["currency"] == "INR"
    assert event.gst["igst_paise"] == 18000


# --- Data stays in asia-south1 -----------------------------------------------------------------


def test_residency_storage_routes_an_indian_tenant_to_the_indian_bucket() -> None:
    """Checklist: 'data stays in asia-south1 (verify bucket ... locations)' — the code half."""
    assert bucket_for_region(SETTINGS, Region.IN) == SETTINGS.s3_bucket_in == "bidradar-in"
    assert bucket_for_region(SETTINGS, Region.US) == SETTINGS.s3_bucket_us == "bidradar-us"
    gcs = Settings(_env_file=None, storage_backend="gcs")  # type: ignore[call-arg]
    assert bucket_for_region(gcs, Region.IN) == gcs.gcs_bucket_in

    router = StorageRouter(SETTINGS)
    assert router.for_region(Region.IN) is router.for_region("in")
    assert router.for_region(Region.IN) is not router.for_region(Region.US)


def test_residency_staging_in_declares_asia_south1_for_the_database_and_the_buckets() -> None:
    """Checklist: '... (verify bucket and DB locations)' — the declared half.

    `terraform output residency` is the live check and is a manual runbook row; this test
    keeps the declaration honest so the live check cannot come as a surprise.
    """
    env = REPO / "infra" / "terraform" / "envs" / "staging-in"
    with (env / "terraform.tfvars").open() as handle:
        tfvars = hcl2.load(handle)
    assert _unquote(tfvars["region"]) == "asia-south1"

    with (env / "main.tf").open() as handle:
        module = next(iter(hcl2.load(handle)["module"][0].values()))
    assert _unquote(module["residency"]) == "in"
    assert _unquote(module["environment"]) == "staging-in"
    assert module["region"] == "${var.region}"
    # Indian portals only: no US adapter is scheduled from Mumbai
    assert [_unquote(r) for r in module["scheduled_adapter_regions"]] == ["in"]

    # one region variable drives the bucket, its CMEK key, Cloud SQL and its backups
    env_main = REPO / "infra" / "terraform" / "modules" / "environment" / "main.tf"
    text = env_main.read_text()
    assert 'check "residency_matches_region"' in text
    assert 'expected_region = var.residency == "in" ? "asia-south1" : "us-east1"' in text
    with env_main.open() as handle:
        modules = {
            name: body for entry in hcl2.load(handle)["module"] for name, body in entry.items()
        }
    assert modules[_quoted(modules, "gcs")]["location"] == "${var.region}"
    sql = modules[_quoted(modules, "cloud_sql")]
    assert sql["region"] == "${var.region}"
    assert sql["backup_location"] == "${var.region}"  # backups never leave India
    secrets = modules[_quoted(modules, "secrets")]
    assert secrets["replication_location"] == "${var.region}"

    outputs = (env / "outputs.tf").read_text()
    for name in ("residency", "residency_ok", "backups"):
        assert f'output "{name}"' in outputs


def _unquote(value: Any) -> Any:
    """python-hcl2 keeps the source quotes on literal strings."""
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _quoted(blocks: dict[str, Any], name: str) -> str:
    """The key `name` has in a python-hcl2 block map (it may keep the source quotes)."""
    for key in blocks:
        if _unquote(key) == name:
            return key
    raise AssertionError(f"no module {name!r} in the environment module")
