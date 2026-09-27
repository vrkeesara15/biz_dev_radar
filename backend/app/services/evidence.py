"""Profile evidence records an agent may cite (SPEC 8 grounding).

Every factual claim a draft makes about the company must point at a record: a past
performance, a service line, a certification, a person or a boilerplate block. This
module loads those records for one profile and gives each a citation token
(app.core.citations), so the outline's win themes, the drafters' claims and the
grounding validator all talk about the same identifiers.

    records = await load_evidence(session, profile_id)
    tokens = evidence_tokens(records)          # what a citation is allowed to resolve to
    prompt_block = render_evidence(records)    # one line per record for the prompt
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.citations import profile_token
from app.core.html_text import html_to_text
from app.models import (
    BoilerplateBlock,
    Certification,
    PastPerformance,
    Personnel,
    ServiceLine,
)

PAST_PERFORMANCE = "past_performance"
SERVICE_LINE = "service_line"
CERTIFICATION = "certification"
PERSONNEL = "personnel"
BOILERPLATE = "boilerplate"
EVIDENCE_TYPES: tuple[str, ...] = (
    PAST_PERFORMANCE,
    SERVICE_LINE,
    CERTIFICATION,
    PERSONNEL,
    BOILERPLATE,
)

DEFAULT_LIMIT = 40
SUMMARY_CHARS = 400


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    source_type: str
    source_id: uuid.UUID
    title: str
    summary: str
    # the full text a drafter may quote (boilerplate body, past-performance scope)
    body: str = ""

    @property
    def token(self) -> str:
        return profile_token(self.source_type, self.source_id)

    def line(self) -> str:
        return f"[{self.token}] {self.source_type}: {self.title} -- {self.summary}"


def _clip(text: str | None, limit: int = SUMMARY_CHARS) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


async def load_evidence(
    session: AsyncSession, profile_id: uuid.UUID, *, limit: int = DEFAULT_LIMIT
) -> list[EvidenceRecord]:
    """The profile's citable records, newest first within each kind (RLS-scoped)."""
    out: list[EvidenceRecord] = []
    past = (
        (
            await session.execute(
                select(PastPerformance)
                .where(PastPerformance.profile_id == profile_id)
                .order_by(PastPerformance.period_end.desc().nullslast(), PastPerformance.title)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for row in past:
        customer = "an anonymised customer" if row.customer_anonymized else row.customer
        value = f"{row.value_amount} {row.value_currency}" if row.value_amount else "value n/a"
        out.append(
            EvidenceRecord(
                source_type=PAST_PERFORMANCE,
                source_id=row.id,
                title=row.title,
                summary=_clip(
                    f"{customer}; role {row.role}; {value}; "
                    f"{row.period_start or '?'} to {row.period_end or '?'}; {row.scope}"
                ),
                body=_clip(f"{row.scope} {row.outcomes or ''}", 2000),
            )
        )
    lines = (
        (
            await session.execute(
                select(ServiceLine)
                .where(ServiceLine.profile_id == profile_id)
                .order_by(ServiceLine.name)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for line_row in lines:
        out.append(
            EvidenceRecord(
                source_type=SERVICE_LINE,
                source_id=line_row.id,
                title=line_row.name,
                summary=_clip(
                    f"{line_row.description}; differentiators: "
                    f"{', '.join(line_row.differentiators or [])}"
                ),
                body=_clip(line_row.description, 2000),
            )
        )
    certs = (
        (
            await session.execute(
                select(Certification)
                .where(Certification.profile_id == profile_id)
                .order_by(Certification.kind)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for cert in certs:
        out.append(
            EvidenceRecord(
                source_type=CERTIFICATION,
                source_id=cert.id,
                title=str(cert.kind),
                summary=_clip(
                    f"number {cert.cert_number or 'n/a'}; level {cert.level or 'n/a'}; "
                    f"issued by {cert.issued_by or 'n/a'}; expires {cert.expires_on or 'n/a'}"
                ),
            )
        )
    people = (
        (
            await session.execute(
                select(Personnel)
                .where(Personnel.profile_id == profile_id)
                .order_by(Personnel.is_key_personnel.desc(), Personnel.name)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for person in people:
        out.append(
            EvidenceRecord(
                source_type=PERSONNEL,
                source_id=person.id,
                title=person.name,
                summary=_clip(
                    f"{person.role}; {person.years_experience or '?'} years; "
                    f"clearances {', '.join(person.clearances or []) or 'none'}; "
                    f"certifications {', '.join(person.certifications or []) or 'none'}"
                ),
            )
        )
    blocks = (
        (
            await session.execute(
                select(BoilerplateBlock)
                .where(BoilerplateBlock.profile_id == profile_id)
                .order_by(BoilerplateBlock.kind, BoilerplateBlock.title)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for block in blocks:
        text = html_to_text(block.body) if block.body_format == "html" else block.body
        out.append(
            EvidenceRecord(
                source_type=BOILERPLATE,
                source_id=block.id,
                title=block.title,
                summary=_clip(f"{block.kind}: {text}"),
                body=_clip(text, 4000),
            )
        )
    return out


def evidence_tokens(records: Sequence[EvidenceRecord]) -> set[str]:
    return {record.token for record in records}


def render_evidence(records: Sequence[EvidenceRecord]) -> str:
    if not records:
        return (
            "(this profile has no past performance, service lines, certifications, "
            "people or boilerplate on file yet)"
        )
    return "\n".join(record.line() for record in records)


def evidence_by_token(records: Sequence[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    return {record.token: record for record in records}
