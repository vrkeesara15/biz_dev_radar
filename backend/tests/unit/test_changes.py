"""M2-09: content hash, diff and change classification (pure)."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.changes import (
    canonical_payload,
    classify_changes,
    content_hash,
    diff_payloads,
    document_hash,
)
from app.core.money import to_usd_or_none
from app.core.opportunity import (
    DetailStatus,
    DocumentKind,
    DocumentRef,
    NoticeType,
    OpportunityIn,
    OpportunityStatus,
)


def _opp(**overrides: object) -> OpportunityIn:
    values: dict[str, object] = {
        "source_id": "sam_opps",
        "external_id": "n1",
        "region": "us",
        "country": "US",
        "currency": "USD",
        "notice_type": NoticeType.RFP,
        "title": "Widgets",
        "naics": ["541512"],
        "response_due_at": datetime(2026, 10, 1, 17, 0, tzinfo=UTC),
        "estimated_value_max": Decimal("100.50"),
        "documents": [DocumentRef(url="https://x/b.pdf"), DocumentRef(url="https://x/a.pdf")],
        "extra": {"type_code": "o", "raw_ref": "raw/x/1"},
    }
    values.update(overrides)
    return OpportunityIn(**values)  # type: ignore[arg-type]


def test_hash_is_stable_and_ignores_volatile_fields() -> None:
    base = _opp()
    assert content_hash(base) == content_hash(_opp())
    assert len(content_hash(base)) == 64
    # document order, raw_ref, detail_status and parent linkage do not matter
    assert content_hash(
        _opp(documents=[DocumentRef(url="https://x/a.pdf"), DocumentRef(url="https://x/b.pdf")])
    ) == content_hash(base)
    assert content_hash(_opp(extra={"type_code": "o", "raw_ref": "raw/x/2"})) == content_hash(base)
    assert content_hash(_opp(detail_status=DetailStatus.FULL)) == content_hash(base)
    assert content_hash(_opp(parent_external_id="n0")) == content_hash(base)
    # real changes do
    assert content_hash(_opp(title="Widgets v2")) != content_hash(base)
    assert content_hash(_opp(response_due_at=datetime(2026, 10, 2, tzinfo=UTC))) != content_hash(
        base
    )
    assert content_hash(_opp(documents=[DocumentRef(url="https://x/a.pdf")])) != content_hash(base)
    assert content_hash(_opp(extra={"type_code": "k"})) != content_hash(base)


def test_document_hashes_use_sha256_when_known() -> None:
    assert (
        document_hash(DocumentRef(url="https://x/a.pdf"))
        == hashlib.sha256(b"https://x/a.pdf").hexdigest()
    )
    assert document_hash(DocumentRef(url="https://x/a.pdf", sha256="ab" * 32)) == "ab" * 32
    assert document_hash({"url": "https://x/a.pdf", "hash": "cd" * 32}) == "cd" * 32
    # a re-download that reveals the sha256 changes the hash exactly once
    assert content_hash(_opp(documents=[DocumentRef(url="https://x/a.pdf")])) != content_hash(
        _opp(documents=[DocumentRef(url="https://x/a.pdf", sha256="ab" * 32)])
    )


def test_canonical_payload_is_json_safe_and_sorted() -> None:
    payload = canonical_payload(_opp())
    assert payload["estimated_value_max"] == "100.5"
    assert payload["response_due_at"] == "2026-10-01T17:00:00+00:00"
    assert payload["notice_type"] == "rfp"
    assert [d["url"] for d in payload["documents"]] == ["https://x/a.pdf", "https://x/b.pdf"]
    assert "raw_ref" not in payload["extra"] and "detail_status" not in payload
    # row-shaped mappings (from the database) hash identically to the pydantic model
    as_mapping = _opp().model_dump(mode="python")
    assert content_hash(as_mapping) == content_hash(_opp())


def test_diff_and_classification() -> None:
    old = canonical_payload(_opp())
    moved = canonical_payload(
        _opp(
            response_due_at=datetime(2026, 10, 8, 17, 0, tzinfo=UTC),
            documents=[
                DocumentRef(url="https://x/b.pdf"),
                DocumentRef(url="https://x/a.pdf"),
                DocumentRef(url="https://x/Q&A_round1.pdf", file_name="Q&A round 1.pdf"),
            ],
        )
    )
    diff = diff_payloads(old, moved)
    assert set(diff) == {"response_due_at", "documents"}
    assert diff["response_due_at"] == {
        "old": "2026-10-01T17:00:00+00:00",
        "new": "2026-10-08T17:00:00+00:00",
    }
    assert classify_changes(diff) == ["deadline_moved", "new_attachment", "qa_posted"]
    cancelled = diff_payloads(old, canonical_payload(_opp(status=OpportunityStatus.CANCELLED)))
    assert classify_changes(cancelled) == ["cancelled"]
    awarded = diff_payloads(old, canonical_payload(_opp(status=OpportunityStatus.AWARDED)))
    assert classify_changes(awarded) == ["awarded"]
    described = diff_payloads(old, canonical_payload(_opp(description_text="Full text")))
    assert classify_changes(described) == ["description_updated"]
    other = diff_payloads(old, canonical_payload(_opp(title="Renamed")))
    assert classify_changes(other) == ["other"]
    assert classify_changes({}) == []
    qa_kind = diff_payloads(
        old,
        canonical_payload(
            _opp(
                documents=[
                    *_opp().documents,
                    DocumentRef(url="https://x/c.pdf", kind=DocumentKind.QA),
                ]
            )
        ),
    )
    assert "qa_posted" in classify_changes(qa_kind)
    questions = diff_payloads(
        old,
        canonical_payload(_opp(questions_due_at=datetime(2026, 9, 30, tzinfo=UTC) + timedelta(0))),
    )
    assert classify_changes(questions) == ["deadline_moved"]


def test_to_usd_or_none() -> None:
    rates = {"USD": 1.0, "INR": 0.012}
    assert to_usd_or_none(Decimal("100"), "USD", rates) == Decimal("100.00")
    assert to_usd_or_none(Decimal("1250000"), "inr", rates) == Decimal("15000.00")
    assert to_usd_or_none(Decimal("1"), "EUR", rates) is None
    assert to_usd_or_none(None, "USD", rates) is None
    assert to_usd_or_none(Decimal("1"), None, rates) is None
