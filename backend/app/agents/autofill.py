"""Autofill extraction (M1-10): one Haiku-class call reads a company web page or the first
pages of a capability statement into `core.autofill.AutofillExtraction`.

    result = await extract_company_facts(llm, settings=settings, content=website_blocks(url, text))
    extraction: AutofillExtraction = result.parsed

Page text and PDF pages are DATA: each is wrapped in an <untrusted> block (with the page
number as the label so citations survive), the system prompt carries the injection
preamble, and the output is the strict JSON schema.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agents.llm import CacheBlock, LLMResult
from app.agents.prompting import system_prompt, untrusted_block
from app.agents.routing import AgentRole, model_for
from app.core.autofill import AutofillExtraction
from app.core.config import Settings

MAX_PAGES = 12
MAX_CONTENT_CHARS = 40_000
MAX_OUTPUT_TOKENS = 4_096

INSTRUCTIONS = """You extract facts about ONE company from its own public web page or its
capability statement so a bid manager can pre-fill a company profile. Report only what the
text states about that company: legal name, trade names (DBA), addresses (with a 2-letter
ISO country code), website, main phone, year founded, legal structure, UEI (12 characters),
CAGE code (5 characters), NAICS and PSC codes, service lines (name + what is delivered in
at most 150 words), certifications (only the listed kinds), past performance (title,
customer, scope, role, period) and key personnel (name + role), plus a 2-4 sentence
company overview in the company's own words. For a document, set `page` on every list item
to the page it came from. Put a 0-1 confidence in field_confidence for each scalar field
you fill (1.0 = stated verbatim, lower when inferred). Never invent values; omit fields
the text does not support. Call the emit tool exactly once."""


def website_blocks(url: str, text: str, *, title: str | None = None) -> str:
    parts = []
    if title:
        parts.append(untrusted_block("page_title", title, source=url))
    parts.append(untrusted_block("web_page", text[:MAX_CONTENT_CHARS], source=url))
    return "\n\n".join(parts)


def document_blocks(pages: Sequence[str], *, name: str) -> str:
    """The first MAX_PAGES pages, each in its own block labelled with its page number."""
    parts: list[str] = []
    budget = MAX_CONTENT_CHARS
    for number, page in enumerate(pages[:MAX_PAGES], start=1):
        text = page.strip()
        if not text or budget <= 0:
            continue
        parts.append(untrusted_block(f"page {number}", text[:budget], source=name))
        budget -= len(text)
    return "\n\n".join(parts)


async def extract_company_facts(
    llm: object, *, settings: Settings, content: str, task: str = "the content"
) -> LLMResult:
    """Haiku-class (classification role) JSON extraction; `result.parsed` is the schema."""
    complete_json = getattr(llm, "complete_json")  # noqa: B009 - LLMClient protocol
    result: LLMResult = await complete_json(
        model=model_for(AgentRole.CLASSIFICATION, settings),
        system=system_prompt(INSTRUCTIONS),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Extract the company facts stated in {task} inside the <untrusted> "
                    "blocks by calling the emit tool."
                ),
            }
        ],
        schema=AutofillExtraction,
        cache_blocks=[CacheBlock(content)],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
    )
    return result
