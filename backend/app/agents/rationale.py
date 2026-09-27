"""SPEC 6 stage 3: the match rationale (M4-05).

    result = await match_rationale(
        llm, settings=settings, notice=NoticeBrief(...), company=CompanyBrief(...),
        score=74, band="high", breakdown={...})
    result.parsed        # a validated Rationale

Only scores >= 50 reach the model (`app.services.matching.rationale` enforces that); the
model id comes from `LLM_MODEL_RATIONALE` through `model_for(AgentRole.RATIONALE)` so no
literal ever appears here (OQ-9).

The notice, its documents and the extracted eligibility are DATA copied from a public
portal, so every part of them is wrapped in an <untrusted> block and the system prompt
carries the injection preamble (SPEC 11). The company profile and the computed score
breakdown are ours and stay outside the wrapper. Document excerpts are page-tagged
("[page 7]") so `eligibility_risks[].page` can cite them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.agents.llm import CacheBlock, LLMResult
from app.agents.prompting import system_prompt, untrusted_block
from app.agents.routing import AgentRole, model_for
from app.core.config import Settings

FIT_BULLETS = 3
MAX_BULLET_CHARS = 280
MAX_ITEMS = 8
MAX_DESCRIPTION_CHARS = 8_000
MAX_DOC_CHARS = 4_000
MAX_DOCS = 3
FIRST_PAGES = 4
MAX_TOKENS = 2_000

FixType = Literal["teaming", "hire", "certification", "other"]
RecommendedAction = Literal["pursue", "watch", "pass"]

INSTRUCTIONS = f"""You advise a business-development team on whether one public
procurement notice is worth pursuing. You are given the company's own profile (trusted)
and the notice with its documents (untrusted data). A rules-based fit score and its
per-signal breakdown are also given; explain and qualify that score, do not recompute it.

Return, by calling the emit tool:
- fit_summary: exactly {FIT_BULLETS} bullets, each one plain sentence under
  {MAX_BULLET_CHARS} characters, saying why this notice does or does not fit THIS company.
- matched_capabilities: the company's service lines, codes, certifications or past
  performance that the notice actually asks for. Name only things present in the profile.
- gaps: what the company is missing for a credible bid. Each gap carries a concrete
  suggested_fix and a fix_type of teaming, hire, certification or other.
- eligibility_risks: conditions that could disqualify the bid (set-aside, turnover,
  experience, registrations, certifications, bonding, local content). Cite the page and
  document name when the condition comes from a document excerpt; use null for both when
  it comes from the notice body or the structured fields.
- recommended_action: pursue, watch or pass.
- confidence: 0 to 1, how sure you are given the evidence you were shown.

Never invent company facts: if the profile does not state something, it is a gap, not a
capability. Never state a requirement the notice does not contain. Say "not stated" rather
than guessing."""


class Gap(BaseModel):
    gap: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)
    fix_type: FixType = "other"


class EligibilityRisk(BaseModel):
    risk: str = Field(min_length=1)
    page: int | None = None
    document: str | None = None


class Rationale(BaseModel):
    """SPEC 6 stage 3 output; stored verbatim in matches.rationale."""

    fit_summary: list[str] = Field(min_length=FIT_BULLETS, max_length=FIT_BULLETS)
    matched_capabilities: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    gaps: list[Gap] = Field(default_factory=list, max_length=MAX_ITEMS)
    eligibility_risks: list[EligibilityRisk] = Field(default_factory=list, max_length=MAX_ITEMS)
    recommended_action: RecommendedAction
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("fit_summary")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(line.split()) for line in value]
        if any(not line for line in cleaned):
            raise ValueError("every fit_summary bullet must be non-empty")
        return [line[:MAX_BULLET_CHARS] for line in cleaned]


@dataclass(frozen=True, slots=True)
class DocumentPages:
    name: str
    pages: list[str]


@dataclass(frozen=True, slots=True)
class NoticeBrief:
    """The untrusted half: what the portal published."""

    title: str
    notice_type: str
    buyer: str | None = None
    summary: str | None = None
    description: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    eligibility: dict[str, Any] = field(default_factory=dict)
    documents: Sequence[DocumentPages] = ()


@dataclass(frozen=True, slots=True)
class CompanyBrief:
    """The trusted half: our own profile, already loaded by the caller."""

    legal_name: str
    region: str
    service_lines: Sequence[str] = ()
    codes: dict[str, Sequence[str]] = field(default_factory=dict)
    certifications: Sequence[str] = ()
    registrations: Sequence[str] = ()
    past_performance: Sequence[str] = ()
    include_keywords: Sequence[str] = ()
    year_founded: int | None = None
    employee_count: int | None = None
    avg_receipts_usd: str | None = None
    target_geography: Sequence[str] = ()


def _lines(pairs: Sequence[tuple[str, Any]]) -> str:
    out: list[str] = []
    for label, value in pairs:
        if value in (None, "", [], {}, ()):
            continue
        if isinstance(value, list | tuple):
            joined = ", ".join(str(v) for v in value if str(v).strip())
            if not joined:
                continue
            out.append(f"{label}: {joined}")
        else:
            out.append(f"{label}: {value}")
    return "\n".join(out)


def notice_block(notice: NoticeBrief) -> str:
    """Every part of the notice, each inside its own <untrusted> wrapper."""
    parts = [untrusted_block("title", notice.title)]
    if notice.buyer:
        parts.append(untrusted_block("buyer", notice.buyer))
    facts = _lines([("notice_type", notice.notice_type), *sorted(notice.facts.items())])
    if facts:
        parts.append(untrusted_block("structured_fields", facts))
    if notice.eligibility:
        parts.append(
            untrusted_block("extracted_eligibility", _lines(sorted(notice.eligibility.items())))
        )
    if notice.summary:
        parts.append(untrusted_block("summary", notice.summary))
    if notice.description:
        parts.append(untrusted_block("description", notice.description[:MAX_DESCRIPTION_CHARS]))
    for doc in list(notice.documents)[:MAX_DOCS]:
        tagged = "\n".join(
            f"[page {number}]\n{page}" for number, page in enumerate(doc.pages[:FIRST_PAGES], 1)
        )[:MAX_DOC_CHARS]
        if tagged.strip():
            parts.append(untrusted_block(f"document:{doc.name}", tagged))
    return "\n\n".join(parts)


def company_block(company: CompanyBrief) -> str:
    """Our own profile: trusted, so it is NOT wrapped."""
    codes = "; ".join(
        f"{scheme}: {', '.join(values)}"
        for scheme, values in sorted(company.codes.items())
        if values
    )
    return "COMPANY PROFILE (trusted, our own records)\n" + _lines(
        [
            ("Legal name", company.legal_name),
            ("Region", company.region),
            ("Founded", company.year_founded),
            ("Employees", company.employee_count),
            ("Average annual receipts (USD)", company.avg_receipts_usd),
            ("Codes", codes),
            ("Service lines", list(company.service_lines)),
            ("Certifications", list(company.certifications)),
            ("Registrations", list(company.registrations)),
            ("Past performance", list(company.past_performance)),
            ("Keywords the owner watches", list(company.include_keywords)),
            ("Target geography", list(company.target_geography)),
        ]
    )


def score_block(score: float | int, band: str, breakdown: dict[str, Any]) -> str:
    """The rules-based score the model must explain, not recompute."""
    signals = breakdown.get("signals") or {}
    rows = [
        f"- {name}: raw {entry.get('raw')} x weight {entry.get('weight')} = "
        f"{entry.get('weighted')}" + (f" ({entry['note']})" if entry.get("note") else "")
        for name, entry in sorted(signals.items())
    ]
    criteria = breakdown.get("eligibility") or []
    checks = [
        f"- {c.get('name')}: {c.get('status')}" + (f" ({c['reason']})" if c.get("reason") else "")
        for c in criteria
        if isinstance(c, dict)
    ]
    out = [f"FIT SCORE (rules, trusted): {score} / 100, band {band}", *rows]
    if checks:
        out += ["ELIGIBILITY CHECKS (rules):", *checks]
    if breakdown.get("label"):
        out.append(f"LABEL: {breakdown['label']} (score capped at {breakdown.get('cap')})")
    return "\n".join(out)


async def match_rationale(
    llm: Any,
    *,
    settings: Settings,
    notice: NoticeBrief,
    company: CompanyBrief,
    score: float | int,
    band: str,
    breakdown: dict[str, Any],
) -> LLMResult:
    """One LLM_MODEL_RATIONALE call returning an LLMResult whose `parsed` is a Rationale."""
    bundle = "\n\n".join(
        [company_block(company), score_block(score, band, breakdown), notice_block(notice)]
    )
    result: LLMResult = await llm.complete_json(
        model=model_for(AgentRole.RATIONALE, settings),
        system=system_prompt(INSTRUCTIONS),
        messages=[
            {
                "role": "user",
                "content": (
                    "Assess this notice for this company and call the emit tool with "
                    f"exactly {FIT_BULLETS} fit_summary bullets."
                ),
            }
        ],
        schema=Rationale,
        cache_blocks=[CacheBlock(bundle)],
        max_tokens=MAX_TOKENS,
    )
    return result
