"""summary_ai (SPEC 5.3, M2-13): a five-line Haiku-class summary of a notice.

    lines = await summarize_opportunity(ctx.llm, title=..., description=..., pages=[...])

Title, description and the first pages of parsed documents are DATA: each part is wrapped
in an <untrusted> block inside one cache block (stable across retries and re-runs), and
the system prompt carries the injection preamble. The output schema forces exactly five
non-empty lines.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agents.llm import CacheBlock, LLMResult
from app.agents.prompting import system_prompt, untrusted_block
from app.agents.routing import AgentRole, model_for
from app.core.config import Settings

LINE_COUNT = 5
MAX_LINE_CHARS = 240
MAX_DESCRIPTION_CHARS = 8_000
MAX_DOC_CHARS = 3_000
MAX_DOCS = 3
FIRST_PAGES = 2

INSTRUCTIONS = f"""You write short, factual summaries of public procurement notices for
business-development teams. Using only the notice content provided, produce
exactly {LINE_COUNT} lines, in this order:
1. What is being procured (scope, quantities, place of performance if stated).
2. Who is buying (agency / department / office) and the notice type or stage.
3. Key eligibility or set-aside / reservation conditions, or "No eligibility conditions stated".
4. Money: estimated value, EMD / fees, funding, or "No value stated".
5. Key dates: questions due, pre-bid, response deadline, or "No dates stated".
Each line is one plain sentence under {MAX_LINE_CHARS} characters, no markdown, no
numbering. Never invent facts; say what is not stated."""


class FiveLineSummary(BaseModel):
    lines: list[str] = Field(min_length=LINE_COUNT, max_length=LINE_COUNT)

    @field_validator("lines")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(line.split()) for line in value]
        if any(not line for line in cleaned):
            raise ValueError("every summary line must be non-empty")
        return [line[:MAX_LINE_CHARS] for line in cleaned]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class DocumentExcerpt(BaseModel):
    name: str
    pages: list[str]


def solicitation_bundle(
    *,
    title: str,
    description: str | None,
    documents: Sequence[DocumentExcerpt] = (),
    buyer: str | None = None,
    facts: dict[str, Any] | None = None,
) -> str:
    """The untrusted content block: title, buyer, structured facts, description, first
    pages of up to MAX_DOCS documents."""
    parts = [untrusted_block("title", title)]
    if buyer:
        parts.append(untrusted_block("buyer", buyer))
    if facts:
        lines = [f"{k}: {v}" for k, v in facts.items() if v not in (None, "", [], {})]
        if lines:
            parts.append(untrusted_block("structured_fields", "\n".join(lines)))
    if description:
        parts.append(untrusted_block("description", description[:MAX_DESCRIPTION_CHARS]))
    for doc in list(documents)[:MAX_DOCS]:
        text = "\n".join(doc.pages[:FIRST_PAGES])[:MAX_DOC_CHARS]
        if text.strip():
            parts.append(untrusted_block(f"document:{doc.name}", text))
    return "\n\n".join(parts)


async def summarize_opportunity(
    llm: Any,
    *,
    settings: Settings,
    title: str,
    description: str | None,
    documents: Sequence[DocumentExcerpt] = (),
    buyer: str | None = None,
    facts: dict[str, Any] | None = None,
) -> LLMResult:
    """One Haiku-class call returning an LLMResult whose `parsed` is a FiveLineSummary."""
    bundle = solicitation_bundle(
        title=title, description=description, documents=documents, buyer=buyer, facts=facts
    )
    result: LLMResult = await llm.complete_json(
        model=model_for(AgentRole.SUMMARY, settings),
        system=system_prompt(INSTRUCTIONS),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Summarise the notice in the <untrusted> blocks in exactly {LINE_COUNT} "
                    "lines by calling the emit tool."
                ),
            }
        ],
        schema=FiveLineSummary,
        cache_blocks=[CacheBlock(bundle)],
        max_tokens=600,
    )
    return result
