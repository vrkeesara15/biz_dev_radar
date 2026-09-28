"""Drafting feedback: what humans changed about agent drafts (SPEC 8).

`services.drafts.save_version` writes one `draft_feedback` row per human edit (the
unified diff against the version the writer started from). This module reads them back:

    rows = await for_section(session, pursuit_id, "technical-approach")
    pairs = await recent_examples(session, tenant_id, "technical-approach", k=3)

`recent_examples` is what a later drafting prompt will use as few-shot examples. It is
tenant-scoped twice over -- the session carries the tenant's RLS setting AND the query
filters on `tenant_id` -- because one tenant's edited proposal text must never reach
another tenant's model call (SPEC 8: "tenant data is never used across tenants").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.diffs import example_pair
from app.models import Draft, DraftFeedback, DraftVersion

DEFAULT_EXAMPLES = 3
MAX_EXAMPLES = 20


@dataclass(frozen=True, slots=True)
class FeedbackExample:
    """One (agent wrote this, a human preferred that) pair for a future prompt."""

    section_id: str
    before: str
    after: str
    diff_text: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "section_id": self.section_id,
            "before": self.before,
            "after": self.after,
            "diff_text": self.diff_text,
        }


async def for_section(
    session: AsyncSession, pursuit_id: uuid.UUID, section_id: str
) -> list[DraftFeedback]:
    """Every recorded edit of one section of one pursuit, newest first."""
    return list(
        (
            await session.execute(
                select(DraftFeedback)
                .where(
                    DraftFeedback.pursuit_id == pursuit_id,
                    DraftFeedback.section_id == section_id,
                )
                .order_by(DraftFeedback.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def for_pursuit(session: AsyncSession, pursuit_id: uuid.UUID) -> list[DraftFeedback]:
    return list(
        (
            await session.execute(
                select(DraftFeedback)
                .where(DraftFeedback.pursuit_id == pursuit_id)
                .order_by(DraftFeedback.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def recent_examples(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    section_kind: str,
    k: int = DEFAULT_EXAMPLES,
) -> list[FeedbackExample]:
    """The tenant's last `k` human edits of this kind of section, as (before, after).

    `section_kind` is the outline section id (technical-approach, past-performance ...),
    which is stable across pursuits, so the examples come from comparable sections. Only
    edits that improved an AGENT draft are returned: a writer editing another writer is
    not a lesson about how the agent should have written it.
    """
    limit = max(1, min(int(k or 0) or DEFAULT_EXAMPLES, MAX_EXAMPLES))
    before = DraftVersion.__table__.alias("before_version")
    after = DraftVersion.__table__.alias("after_version")
    rows = (
        await session.execute(
            select(
                DraftFeedback.section_id,
                before.c.body_text,
                after.c.body_text,
                DraftFeedback.diff_text,
            )
            .join(before, before.c.id == DraftFeedback.from_version_id)
            .join(after, after.c.id == DraftFeedback.to_version_id)
            .where(
                DraftFeedback.tenant_id == tenant_id,
                DraftFeedback.section_id == section_kind,
                DraftFeedback.from_author == "agent",
            )
            .order_by(DraftFeedback.created_at.desc())
            .limit(limit)
        )
    ).all()
    examples: list[FeedbackExample] = []
    for section_id, before_text, after_text, diff_text in rows:
        first, second = example_pair(str(before_text or ""), str(after_text or ""))
        examples.append(
            FeedbackExample(
                section_id=str(section_id),
                before=first,
                after=second,
                diff_text=str(diff_text or ""),
            )
        )
    return examples


async def section_kinds(session: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """Section ids this tenant has edit feedback for (what `recent_examples` can answer)."""
    rows = (
        await session.execute(
            select(Draft.section_id)
            .join(DraftFeedback, DraftFeedback.draft_id == Draft.id)
            .where(DraftFeedback.tenant_id == tenant_id)
            .distinct()
            .order_by(Draft.section_id)
        )
    ).scalars()
    return [str(row) for row in rows]
