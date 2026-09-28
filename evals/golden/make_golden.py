"""Build the synthetic golden set (SPEC 12: 10 US solicitations + 10 Indian tenders with
hand-labelled requirements and eligibility fields).

    cd backend && uv run python ../evals/golden/make_golden.py

Every item lands in `evals/golden/<region>/<slug>/` as:

  notice.pdf     the solicitation, 3-4 pages, one extractor batch
  labels.json    the labelled requirements (text, page, type) + the eligibility fields
  recorded.json  the recorded extractor answer replayed by FakeLLM (one batch)

SYNTHETIC: every notice here was written for this eval. They follow the shape of the
real thing (a DoD RFP with Sections L and M, a GeM bid with EMD and turnover, a GePNIC
tender in a Hindi/English mix) without reproducing any real notice's text.

The recorded answers are deliberately NOT copies of the labels -- `restate()` rewrites
each requirement in the extractor's own words so the fuzzy scorer is exercised, and each
item deliberately misses a label, over-extracts a real but non-binding sentence, repeats
one obligation and emits items the citation validator must reject. The pass bars
therefore measure the extractor's validator and dedupe, not a replay.

`evals/golden/us/irs_sources_sought` predates this script and keeps its own generator.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
LINE_WIDTH = 92
FONT_SIZE = 10
LEADING = 14
MARGIN = 72
MAX_BATCH_CHARS = 12_000

PROSE = "p"
REQUIREMENT_TYPES = (
    "shall",
    "must",
    "should",
    "eligibility",
    "format",
    "submission",
    "evaluation",
)

VOLUMES = {
    "eligibility": "Eligibility",
    "format": "Submission Package",
    "submission": "Submission Package",
    "evaluation": "Technical",
    "shall": "Technical",
    "must": "Technical",
    "should": "Technical",
}

Page = tuple[str, list[tuple[str, str]]]


@dataclass(slots=True)
class Item:
    slug: str  # "<region>/<name>"
    notice_id: str
    region: str
    notice_type: str
    source: str
    pages: list[Page]
    eligibility: dict[str, object]
    # label ids (L-01 ...) the recorded answer deliberately does not find
    misses: tuple[str, ...] = ()
    # (page, quote, type) of real but non-binding sentences the answer over-extracts
    over_extractions: tuple[tuple[int, str, str], ...] = ()
    # label ids the answer states twice (dedupe must keep one)
    repeats: tuple[str, ...] = ()
    # an item citing a page the batch does not hold, and one with an invented quote
    bad_page: bool = True
    invented_quote: bool = True
    # rendered at low fidelity, the way an OCR pass over a scan reads
    scanned: bool = False
    labels: list[dict[str, object]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.slug.split("/", 1)[1]

    @property
    def directory(self) -> Path:
        return HERE / self.slug


# --- the notice PDF --------------------------------------------------------------------


def _degrade(text: str) -> str:
    """What an OCR pass over a fax-quality scan does to clean text."""
    return (
        text.replace("rn", "m", 1)
        .replace("ll", "II", 1)
        .replace(". ", ".  ", 1)
        .replace("0", "O", 1)
    )


def write_pdf(item: Item) -> None:
    doc = pymupdf.open()
    for heading, rows in item.pages:
        page = doc.new_page()
        y = MARGIN
        page.insert_text((MARGIN, y), heading, fontsize=13, fontname="helv")
        y += LEADING * 1.5
        for _kind, text in rows:
            for line in textwrap.wrap(text, LINE_WIDTH):
                page.insert_text((MARGIN, y), line, fontsize=FONT_SIZE, fontname="helv")
                y += LEADING
            y += LEADING // 2
    item.directory.mkdir(parents=True, exist_ok=True)
    doc.save(item.directory / "notice.pdf", garbage=4, deflate=True)
    doc.close()


# --- labels ---------------------------------------------------------------------------


def build_labels(item: Item) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for page_no, (_heading, entries) in enumerate(item.pages, start=1):
        for kind, text in entries:
            if kind == PROSE:
                continue
            assert kind in REQUIREMENT_TYPES, kind
            rows.append(
                {"id": f"L-{len(rows) + 1:02d}", "text": text, "page": page_no, "type": kind}
            )
    item.labels = rows
    return {
        "source": item.source,
        "notice_id": item.notice_id,
        "region": item.region,
        "notice_type": item.notice_type,
        "requirements": rows,
        "eligibility": item.eligibility,
    }


# --- the recorded answer ------------------------------------------------------------------

# deterministic rewrites: the extractor states an obligation in its own words
_REWRITES: tuple[tuple[str, str], ...] = (
    # the subject noun is kept: dropping it costs a short sentence more similarity than
    # a real paraphrase would, and the scorer would then measure the paraphraser
    ("The contractor shall ", "The contractor is required to "),
    ("The Contractor shall ", "The contractor is required to "),
    ("The contractor must ", "The contractor is required to "),
    ("The supplier shall ", "The supplier is required to "),
    ("The seller shall ", "The seller is required to "),
    ("The agency shall ", "The agency is required to "),
    ("The consultant shall ", "The consultant is required to "),
    ("The awardee shall ", "The awardee is required to "),
    ("The service provider shall ", "The service provider is required to "),
    ("Offerors shall ", "Offerors are required to "),
    ("Offerors must ", "Offerors are required to "),
    ("Bidders shall ", "Bidders are required to "),
    ("Bidders must ", "Bidders are required to "),
    ("Respondents must ", "Respondents are required to "),
    ("The bidder shall ", "The bidder is required to "),
    ("Applicants must ", "Applicants are required to "),
    ("Quoters shall ", "Quoters are required to "),
    ("Proposers must ", "Proposers are required to "),
    ("The Government will ", "The buyer will "),
    ("shall not exceed", "may not exceed"),
    ("must be submitted", "have to be submitted"),
)


def restate(text: str) -> str:
    """Rewrite a labelled requirement the way the extractor would report it."""
    body = text
    # drop a leading clause number such as "4.1 " or "L.3.2 "
    head, _, rest = body.partition(" ")
    if rest and all(part.isdigit() for part in head.replace("L.", "").replace("M.", "").split(".") if part):
        body = rest
    for old, new in _REWRITES:
        if body.startswith(old):
            return new + body[len(old) :]
        if old in body:
            return body.replace(old, new, 1)
    return body


def quote_for(text: str) -> str:
    """A verbatim slice of the sentence; it is on the page, so the validator accepts it."""
    stripped = text
    head, _, rest = stripped.partition(" ")
    if rest and any(ch.isdigit() for ch in head):
        stripped = rest
    words = stripped.split()
    slice_ = words[: max(8, min(len(words) - 1, 16))] if len(words) > 9 else words
    return " ".join(slice_).rstrip(".,;")


def build_recorded(item: Item) -> dict[str, object]:
    by_id = {str(row["id"]): row for row in item.labels}
    for missed in item.misses:
        assert missed in by_id, (item.slug, missed)
    for repeated in item.repeats:
        assert repeated in by_id, (item.slug, repeated)

    predictions: list[dict[str, object]] = []
    for label_id, row in by_id.items():
        if label_id in item.misses:
            continue
        kind = str(row["type"])
        predictions.append(
            {
                "text": restate(str(row["text"])),
                "page": int(row["page"]),  # type: ignore[arg-type]
                "type": kind,
                "volume": VOLUMES[kind],
                "quote": quote_for(str(row["text"])),
                "confidence": 0.9,
            }
        )
    for page, quote, kind in item.over_extractions:
        predictions.append(
            {
                "text": f"{quote[0].upper()}{quote[1:]}.",
                "page": page,
                "type": kind,
                "volume": VOLUMES[kind],
                "quote": quote,
                "confidence": 0.7,
            }
        )
    for label_id in item.repeats:
        row = by_id[label_id]
        kind = str(row["type"])
        predictions.append(
            {
                "text": restate(str(row["text"])) + " (restated in the summary)",
                "page": int(row["page"]),  # type: ignore[arg-type]
                "type": kind,
                "volume": VOLUMES[kind],
                "quote": quote_for(str(row["text"])),
                "confidence": 0.8,
            }
        )
    rejected = 0
    if item.bad_page:
        predictions.append(
            {
                "text": "The contractor shall staff a programme office at the buyer's premises.",
                "page": len(item.pages) + 4,
                "type": "shall",
                "volume": "Technical",
                "quote": "staff a programme office at the buyer's premises",
                "confidence": 1.0,
            }
        )
        rejected += 1
    if item.invented_quote:
        predictions.append(
            {
                "text": "The contractor shall provide unlimited onsite support at every location.",
                "page": 1,
                "type": "shall",
                "volume": "Technical",
                "quote": "unlimited onsite support at every location nationwide",
                "confidence": 1.0,
            }
        )
        rejected += 1
    return {
        "model_class": "opus",
        "expected": {
            "rejected": rejected,
            "duplicates_removed": len(item.repeats),
            "missed_labels": sorted(item.misses),
        },
        "batches": [{"requirements": predictions}],
    }


def write_item(item: Item) -> tuple[int, int]:
    total = sum(len(text) for _h, rows in item.pages for _k, text in rows)
    assert total < MAX_BATCH_CHARS, f"{item.slug} must fit one extractor batch ({total})"
    if item.scanned:
        item.pages = [(h, [(k, _degrade(t)) for k, t in rows]) for h, rows in item.pages]
    write_pdf(item)
    labels = build_labels(item)
    (item.directory / "labels.json").write_text(json.dumps(labels, indent=2) + "\n")
    recorded = build_recorded(item)
    (item.directory / "recorded.json").write_text(json.dumps(recorded, indent=2) + "\n")
    return len(item.labels), len(recorded["batches"][0]["requirements"])  # type: ignore[index]


def main() -> None:
    from us_items import US_ITEMS  # noqa: PLC0415 - the item data lives next to this file
    from in_items import IN_ITEMS  # noqa: PLC0415

    for item in [*US_ITEMS, *IN_ITEMS]:
        labels, predictions = write_item(item)
        print(f"{item.slug}: {labels} labels, {predictions} predicted")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(HERE))
    main()
