"""Privacy logic that needs no I/O (SPEC 11: DPDP consent, data-principal requests,
CCPA-style export/delete, sub-processors).

Holds the consent / request vocabularies, the SLA clock, the public sub-processor list
(kept in step with docs/privacy/sub-processors.md by a test) and the rules that decide
which tables an export or an erasure touches. services.privacy does the database, object
storage and Celery work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

DEFAULT_SLA_DAYS = 30
# audit_log survives an erasure: SPEC 11 requires the trail of who did what to outlive
# the data itself (and the table is append-only for the application role anyway).
ERASURE_RETAINED_TABLES = frozenset({"audit_log"})


class ConsentKind(StrEnum):
    DPDP = "dpdp"
    PRIVACY_POLICY = "privacy_policy"
    TERMS = "terms"


class DataRequestKind(StrEnum):
    ACCESS = "access"
    CORRECTION = "correction"
    ERASURE = "erasure"
    TENANT_EXPORT = "tenant_export"
    TENANT_DELETE = "tenant_delete"


class DataRequestStatus(StrEnum):
    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    REJECTED = "rejected"


# Requests a data principal raises about their own data (POST /me/data-requests).
SELF_SERVICE_KINDS = (
    DataRequestKind.ACCESS,
    DataRequestKind.CORRECTION,
    DataRequestKind.ERASURE,
)
# Requests only a tenant owner may raise; each runs as a background job.
TENANT_KINDS = (DataRequestKind.TENANT_EXPORT, DataRequestKind.TENANT_DELETE)
TERMINAL_STATUSES = (DataRequestStatus.DONE, DataRequestStatus.REJECTED)


def sla_due_at(created_at: datetime, days: int = DEFAULT_SLA_DAYS) -> datetime:
    """Statutory answer-by date. DPDP/CCPA both work in days from receipt."""
    if days < 0:
        raise ValueError("SLA days must be >= 0")
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    return created_at + timedelta(days=days)


def is_overdue(sla_due_at_value: datetime, now: datetime, status: str) -> bool:
    if DataRequestStatus(status) in TERMINAL_STATUSES:
        return False
    return now > sla_due_at_value


@dataclass(frozen=True, slots=True)
class SubProcessor:
    """One third party that may process tenant personal data (SPEC 11, DPDP notice)."""

    name: str
    purpose: str
    data: str
    location: str
    notes: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "data": self.data,
            "location": self.location,
            "notes": self.notes,
        }


# Published at GET /api/v1/privacy and mirrored in docs/privacy/sub-processors.md
# (tests/unit/test_privacy_core.py fails if the two drift apart).
SUB_PROCESSORS: tuple[SubProcessor, ...] = (
    SubProcessor(
        name="Anthropic",
        purpose="LLM inference for summaries, drafting and analysis agents",
        data="Solicitation text, company profile excerpts, prompts and completions",
        location="United States",
        notes=(
            "Contracted under zero-data-retention / no-training terms on the "
            "organisation's Claude API account (SPEC 11)."
        ),
    ),
    SubProcessor(
        name="Voyage AI",
        purpose="Text embeddings for matching and knowledge-base retrieval",
        data="Notice text and knowledge-base chunks",
        location="United States",
    ),
    SubProcessor(
        name="Google Cloud Platform",
        purpose="Application hosting, Postgres, object storage, logging and tracing",
        data="All tenant data, stored in the tenant's residency region",
        location="United States (us) and India, asia-south1 Mumbai (in)",
        notes="Regional buckets and databases; CMEK at rest.",
    ),
    SubProcessor(
        name="Amazon Web Services (SES)",
        purpose="Transactional and digest email delivery",
        data="Recipient email address, name and message content",
        location="United States and India (ap-south-1)",
    ),
    SubProcessor(
        name="SendGrid (Twilio)",
        purpose="Fallback email delivery",
        data="Recipient email address, name and message content",
        location="United States",
    ),
    SubProcessor(
        name="Slack",
        purpose="Alert and approval notifications into a tenant's workspace",
        data="Notice titles, scores and links; the notifying user's name",
        location="United States",
        notes="Only for tenants that install the Slack app.",
    ),
    SubProcessor(
        name="Twilio / Gupshup",
        purpose="WhatsApp Business API delivery",
        data="Recipient phone number and message content",
        location="United States (Twilio) and India (Gupshup)",
        notes="Only for tenants that enable WhatsApp alerts.",
    ),
    SubProcessor(
        name="Stripe",
        purpose="Subscription billing and invoicing for US tenants",
        data="Billing contact, email and payment method (held by Stripe, never by us)",
        location="United States",
    ),
    SubProcessor(
        name="Razorpay",
        purpose="Subscription billing and GST invoicing for Indian tenants",
        data="Billing contact, email, GSTIN and payment method (held by Razorpay)",
        location="India",
    ),
    SubProcessor(
        name="Sentry",
        purpose="Error monitoring",
        data="Stack traces and request metadata with PII scrubbed before sending",
        location="United States",
    ),
    SubProcessor(
        name="Langfuse",
        purpose="LLM trace and cost observability",
        data="Agent prompts, completions, token counts and cost",
        location="European Union",
    ),
)


def sub_processors() -> list[dict[str, str]]:
    return [processor.as_dict() for processor in SUB_PROCESSORS]


def sub_processor_names() -> list[str]:
    return [processor.name for processor in SUB_PROCESSORS]
