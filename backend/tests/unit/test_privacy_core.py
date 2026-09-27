"""M7-07: pure privacy logic — SLA clock, vocabularies, sub-processor list and docs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.paths import tenant_export_key
from app.core.privacy import (
    DEFAULT_SLA_DAYS,
    ERASURE_RETAINED_TABLES,
    SELF_SERVICE_KINDS,
    SUB_PROCESSORS,
    TENANT_KINDS,
    ConsentKind,
    DataRequestKind,
    DataRequestStatus,
    is_overdue,
    sla_due_at,
    sub_processor_names,
    sub_processors,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_sla_default_is_thirty_days_from_receipt() -> None:
    assert DEFAULT_SLA_DAYS == 30
    assert sla_due_at(NOW) == NOW + timedelta(days=30)
    assert sla_due_at(NOW, 7) == NOW + timedelta(days=7)
    assert sla_due_at(NOW, 0) == NOW


def test_sla_rejects_naive_datetimes_and_negative_windows() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        sla_due_at(datetime(2026, 9, 27, 12, 0))
    with pytest.raises(ValueError, match=">= 0"):
        sla_due_at(NOW, -1)


def test_overdue_only_applies_to_open_requests() -> None:
    due = sla_due_at(NOW, 30)
    later = due + timedelta(seconds=1)
    assert is_overdue(due, later, DataRequestStatus.RECEIVED) is True
    assert is_overdue(due, later, DataRequestStatus.IN_PROGRESS) is True
    assert is_overdue(due, later, DataRequestStatus.DONE) is False
    assert is_overdue(due, later, DataRequestStatus.REJECTED) is False
    assert is_overdue(due, due - timedelta(days=1), DataRequestStatus.RECEIVED) is False


def test_request_kind_split() -> None:
    assert set(SELF_SERVICE_KINDS) == {
        DataRequestKind.ACCESS,
        DataRequestKind.CORRECTION,
        DataRequestKind.ERASURE,
    }
    assert set(TENANT_KINDS) == {DataRequestKind.TENANT_EXPORT, DataRequestKind.TENANT_DELETE}
    assert set(SELF_SERVICE_KINDS) | set(TENANT_KINDS) == set(DataRequestKind)
    assert set(ConsentKind) == {
        ConsentKind.DPDP,
        ConsentKind.PRIVACY_POLICY,
        ConsentKind.TERMS,
    }


def test_audit_log_is_the_only_table_kept_through_an_erasure() -> None:
    assert frozenset({"audit_log"}) == ERASURE_RETAINED_TABLES


def test_export_key_is_under_the_tenant_prefix() -> None:
    tenant = uuid.UUID("11111111-1111-1111-1111-111111111111")
    request = uuid.UUID("22222222-2222-2222-2222-222222222222")
    assert tenant_export_key(tenant, request) == f"tenants/{tenant}/exports/{request}.zip"


# --- sub-processors ----------------------------------------------------------------------

REQUIRED_SUB_PROCESSORS = {
    "Anthropic",
    "Voyage AI",
    "Google Cloud Platform",
    "Amazon Web Services (SES)",
    "SendGrid (Twilio)",
    "Slack",
    "Twilio / Gupshup",
    "Stripe",
    "Razorpay",
    "Sentry",
    "Langfuse",
}


def test_every_sub_processor_required_by_the_spec_is_listed() -> None:
    assert set(sub_processor_names()) >= REQUIRED_SUB_PROCESSORS


def test_anthropic_entry_records_the_zero_data_retention_terms() -> None:
    anthropic = next(p for p in SUB_PROCESSORS if p.name == "Anthropic")
    assert "zero-data-retention" in anthropic.notes
    assert "no-training" in anthropic.notes


def test_sub_processor_entries_are_complete() -> None:
    for entry in sub_processors():
        assert set(entry) == {"name", "purpose", "data", "location", "notes"}
        for field in ("name", "purpose", "data", "location"):
            assert entry[field].strip(), f"{entry['name']}.{field} is empty"


def test_sub_processors_doc_matches_the_published_list(repo_root: Path) -> None:
    """docs/privacy/sub-processors.md is the public copy of core.privacy.SUB_PROCESSORS."""
    doc = (repo_root / "docs" / "privacy" / "sub-processors.md").read_text()
    for processor in SUB_PROCESSORS:
        assert f"| {processor.name} |" in doc, f"{processor.name} missing from the doc"
        assert processor.purpose in doc
        assert processor.location in doc


def test_dpdp_notice_states_the_rights_and_the_version(repo_root: Path) -> None:
    notice = (repo_root / "docs" / "privacy" / "dpdp-notice.md").read_text()
    assert "v1" in notice.splitlines()[0]
    for right in ("access", "correct", "erase", "withdraw consent"):
        assert right in notice
    assert "Grievance officer" in notice
    assert "sub-processors.md" in notice
