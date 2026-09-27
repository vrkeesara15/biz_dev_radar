"""M2-01: adapter contract types match SPEC 5.1 / 5.3."""

import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.adapters import base
from app.adapters.base import (
    AdapterHealth,
    AdapterStatus,
    DocumentRef,
    NoticeType,
    OpportunityIn,
    RawRecord,
    SourceAdapter,
)
from app.core.config import Region
from app.core.opportunity import DetailStatus, OpportunityStatus
from pydantic import ValidationError

from tests.adapters.fixture_adapter import FixtureAdapter


def test_protocol_matches_spec_5_1() -> None:
    methods = {"fetch", "fetch_detail", "fetch_documents", "normalize", "health"}
    assert methods <= set(SourceAdapter.__protocol_attrs__)  # type: ignore[attr-defined]
    assert {"source_id", "region", "schedule"} <= set(SourceAdapter.__protocol_attrs__)  # type: ignore[attr-defined]
    fetch = inspect.signature(SourceAdapter.fetch)
    assert list(fetch.parameters) == ["self", "since", "cursor"]
    assert list(inspect.signature(SourceAdapter.fetch_detail).parameters) == ["self", "external_id"]
    assert list(inspect.signature(SourceAdapter.fetch_documents).parameters) == ["self", "raw"]
    assert list(inspect.signature(SourceAdapter.normalize).parameters) == ["self", "raw"]
    assert isinstance(FixtureAdapter(), SourceAdapter)


def test_base_exports_every_contract_type() -> None:
    for name in ("SourceAdapter", "RawRecord", "DocumentRef", "OpportunityIn", "AdapterHealth"):
        assert name in base.__all__
        assert hasattr(base, name)


def test_notice_type_enum_matches_spec_5_3() -> None:
    assert {m.value for m in NoticeType} == {
        "rfi",
        "sources_sought",
        "presolicitation",
        "rfp",
        "rfq",
        "combined",
        "grant",
        "forecast",
        "award",
        "eoi",
        "gem_bid",
        "reverse_auction",
        "corrigendum",
        "special",
    }
    assert {m.value for m in OpportunityStatus} == {
        "open",
        "closing_soon",
        "closed",
        "cancelled",
        "awarded",
    }
    assert {m.value for m in AdapterStatus} == {
        "ok",
        "degraded",
        "failing",
        "not_implemented",
        "robots_disallowed",
    }


def _minimal(**overrides: object) -> OpportunityIn:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": "abc",
        "region": Region.US,
        "country": "us",
        "currency": "usd",
        "notice_type": NoticeType.RFP,
        "title": "Widgets",
    }
    values.update(overrides)
    return OpportunityIn(**values)  # type: ignore[arg-type]


def test_opportunity_in_has_every_spec_5_3_adapter_field() -> None:
    expected = {
        "source_id",
        "external_id",
        "source_url",
        "region",
        "country",
        "currency",
        "notice_type",
        "title",
        "description_text",
        "solicitation_number",
        "buyer_org",
        "buyer_sub_org",
        "buyer_office",
        "buyer_hierarchy",
        "naics",
        "psc",
        "aln",
        "india_category",
        "set_aside",
        "reservation",
        "place_of_performance",
        "estimated_value_min",
        "estimated_value_max",
        "emd_amount",
        "tender_fee",
        "posted_at",
        "questions_due_at",
        "prebid_meeting_at",
        "response_due_at",
        "opening_at",
        "archive_at",
        "source_tz",
        "contacts",
        "eligibility",
        "documents",
        "status",
    }
    assert expected <= set(OpportunityIn.model_fields)
    opp = _minimal()
    assert opp.country == "US" and opp.currency == "USD"
    assert opp.detail_status is DetailStatus.PENDING
    assert opp.documents == [] and opp.naics == []


def test_opportunity_in_requires_tz_aware_datetimes_and_stores_utc() -> None:
    with pytest.raises(ValidationError):
        _minimal(posted_at=datetime(2026, 1, 1, 12, 0))
    ist = timezone(timedelta(hours=5, minutes=30))
    opp = _minimal(
        response_due_at=datetime(2026, 1, 1, 17, 30, tzinfo=ist), source_tz="Asia/Kolkata"
    )
    assert opp.response_due_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert opp.response_due_at.tzinfo is UTC
    assert opp.source_tz == "Asia/Kolkata"


def test_opportunity_in_rejects_unknown_fields_and_bad_enum() -> None:
    with pytest.raises(ValidationError):
        _minimal(notice_type="tender")
    with pytest.raises(ValidationError):
        _minimal(bogus=1)
    with pytest.raises(ValidationError):
        _minimal(title="")


def test_code_lists_are_trimmed_and_deduplicated() -> None:
    opp = _minimal(
        naics=[" 541512", "541512", "", "541511 "], buyer_hierarchy=["DoD", "Army", "DoD"]
    )
    assert opp.naics == ["541512", "541511"]
    assert opp.buyer_hierarchy == ["DoD", "Army"]


def test_raw_record_document_ref_and_health_shapes() -> None:
    now = datetime.now(UTC)
    raw = RawRecord(source_id="s", external_id="e", fetched_at=now, payload={"a": 1})
    assert raw.content_type == "application/json"
    assert raw.raw_ref is None and raw.meta == {}
    doc = DocumentRef(url="https://x/y.pdf", file_name="y.pdf")
    assert doc.kind.value == "attachment"
    with pytest.raises(ValidationError):
        DocumentRef(url="https://x", nope=1)  # type: ignore[call-arg]
    health = AdapterHealth(status=AdapterStatus.DEGRADED, last_run_at=now, message="slow")
    assert not health.ok
    assert AdapterHealth(status=AdapterStatus.OK).ok
