"""Proposal outline and win themes (SPEC 8 agent 5). Pure: shapes and validation only.

The outline mirrors the solicitation's own structure -- Section L (instructions) and
Section M (evaluation factors) in the US, the technical and financial covers in India --
and every compliance item must land in a section or be listed as unmapped.

    outline, report = normalise(raw_outline, req_ids=..., region="us", page_limit=10,
                               evidence_tokens={"PROFILE:past_performance:..."})
    report.warnings        # what the UI and the step output show
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.core.config import Region

MIN_WIN_THEMES = 3
MAX_WIN_THEMES = 5

# The volume names each region's outline is expected to carry, and the aliases that
# count as "present" (the model writes the solicitation's own wording where it has one).
US_SECTION_L_M = ("section l", "section m", "instructions to offerors", "evaluation factors")
IN_TECHNICAL_ALIASES = ("technical", "technical bid", "technical cover")
IN_FINANCIAL_ALIASES = ("financial", "price bid", "financial bid", "commercial", "financial cover")


class OutlineSection(BaseModel):
    """One writable section of one volume. `id` is the key the drafters and the workspace
    API use (drafts.section_id), so it must be unique inside the outline."""

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=2, max_length=200)
    # req ids (R-001 ...) this section answers
    maps_requirements: list[str] = Field(default_factory=list)
    # the evaluation factor it is scored against (Section M / the tender's criteria)
    evaluation_criterion: str | None = Field(default=None, max_length=300)
    page_budget: int | None = Field(default=None, ge=0, le=500)


class OutlineVolume(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    sections: list[OutlineSection] = Field(default_factory=list)


class WinTheme(BaseModel):
    theme: str = Field(min_length=5, max_length=300)
    discriminator: str = Field(min_length=5, max_length=400)
    # tokens from app.core.citations naming the profile records that prove the theme
    evidence_citations: list[str] = Field(default_factory=list)


class Outline(BaseModel):
    volumes: list[OutlineVolume] = Field(default_factory=list)
    win_themes: list[WinTheme] = Field(default_factory=list)
    unmapped_requirements: list[str] = Field(default_factory=list)

    def sections(self) -> list[tuple[str, OutlineSection]]:
        return [(v.name, s) for v in self.volumes for s in v.sections]

    def section_ids(self) -> list[str]:
        return [s.id for _, s in self.sections()]


@dataclass(frozen=True, slots=True)
class OutlineReport:
    mapped: tuple[str, ...] = ()
    unmapped: tuple[str, ...] = ()
    unknown_requirements: tuple[str, ...] = ()  # req ids the model invented
    dropped_citations: tuple[str, ...] = ()  # win-theme evidence that does not resolve
    renamed_sections: tuple[str, ...] = ()  # duplicate ids made unique
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def mentions_section_l_m(texts: Iterable[str]) -> bool:
    """True when the solicitation talks in Section L / M terms (US RFPs)."""
    joined = " ".join(_norm(t) for t in texts)
    return any(alias in joined for alias in US_SECTION_L_M)


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _norm(value)).strip("-")
    return (slug or fallback)[:64]


def _unique_ids(outline: Outline) -> tuple[Outline, list[str]]:
    seen: set[str] = set()
    renamed: list[str] = []
    for index, (_volume, section) in enumerate(outline.sections(), start=1):
        base = _slug(section.id, f"section-{index}")
        candidate = base
        suffix = 2
        while candidate in seen:
            candidate = f"{base}-{suffix}"
            suffix += 1
        if candidate != section.id:
            renamed.append(f"{section.id} -> {candidate}")
        section.id = candidate
        seen.add(candidate)
    return outline, renamed


def normalise(
    outline: Outline,
    *,
    req_ids: Collection[str],
    region: Region | str,
    page_limit: int | None = None,
    evidence_tokens: Collection[str] = (),
) -> tuple[Outline, OutlineReport]:
    """Clean the model's outline and report what is wrong with it.

    - section ids are slugged and made unique (the drafters key on them),
    - requirement references the extractor never produced are dropped,
    - `unmapped_requirements` is recomputed from the compliance set, never trusted,
    - win-theme evidence that does not resolve to a profile record is dropped,
    - the number of win themes and the page budget are checked against SPEC 8 and the
      solicitation's page limit.
    """
    known = set(req_ids)
    outline, renamed = _unique_ids(outline)
    mapped: set[str] = set()
    unknown: set[str] = set()
    for _volume, section in outline.sections():
        kept: list[str] = []
        for ref in section.maps_requirements:
            ref = ref.strip().upper()
            if ref in known:
                kept.append(ref)
                mapped.add(ref)
            elif ref:
                unknown.add(ref)
        section.maps_requirements = sorted(dict.fromkeys(kept))

    allowed_tokens = set(evidence_tokens)
    dropped: list[str] = []
    for theme in outline.win_themes:
        kept_tokens: list[str] = []
        for token in theme.evidence_citations:
            token = token.strip().strip("[]").strip()
            if token in allowed_tokens:
                kept_tokens.append(token)
            elif token:
                dropped.append(token)
        theme.evidence_citations = list(dict.fromkeys(kept_tokens))

    unmapped = sorted(known - mapped)
    outline.unmapped_requirements = unmapped

    warnings: list[str] = []
    if not outline.volumes:
        warnings.append("the outline has no volumes")
    if unmapped:
        warnings.append(
            f"{len(unmapped)} compliance item(s) are not mapped to a section: "
            f"{', '.join(unmapped[:20])}"
        )
    if unknown:
        warnings.append(
            f"dropped {len(unknown)} requirement reference(s) that do not exist: "
            f"{', '.join(sorted(unknown)[:20])}"
        )
    if dropped:
        warnings.append(
            f"dropped {len(dropped)} win-theme citation(s) that resolve to no profile record"
        )
    if renamed:
        warnings.append(f"made {len(renamed)} duplicate section id(s) unique")
    themes = len(outline.win_themes)
    if themes < MIN_WIN_THEMES or themes > MAX_WIN_THEMES:
        warnings.append(f"{themes} win themes: SPEC 8 asks for {MIN_WIN_THEMES}-{MAX_WIN_THEMES}")
    ungrounded = [t.theme for t in outline.win_themes if not t.evidence_citations]
    if ungrounded:
        warnings.append(f"{len(ungrounded)} win theme(s) cite no profile record")
    budget = sum(s.page_budget or 0 for _v, s in outline.sections())
    if page_limit is not None and budget > page_limit:
        warnings.append(
            f"page budgets add up to {budget} pages but the solicitation allows {page_limit}"
        )
    warnings.extend(_region_warnings(outline, region))
    return outline, OutlineReport(
        mapped=tuple(sorted(mapped)),
        unmapped=tuple(unmapped),
        unknown_requirements=tuple(sorted(unknown)),
        dropped_citations=tuple(dropped),
        renamed_sections=tuple(renamed),
        warnings=warnings,
    )


def _region_warnings(outline: Outline, region: Region | str) -> list[str]:
    names = [_norm(v.name) for v in outline.volumes]
    if Region(region) is Region.IN:
        missing = []
        if not any(any(a in n for a in IN_TECHNICAL_ALIASES) for n in names):
            missing.append("technical")
        if not any(any(a in n for a in IN_FINANCIAL_ALIASES) for n in names):
            missing.append("financial")
        if missing:
            return [
                "an Indian tender is submitted as separate covers; the outline has no "
                f"{' and no '.join(missing)} cover"
            ]
    return []
