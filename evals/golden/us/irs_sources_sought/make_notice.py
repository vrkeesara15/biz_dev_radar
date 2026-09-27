"""Build the synthetic IRS sources-sought golden item (SPEC 12: "incl. the IRS sources-sought
notice pattern"). SYNTHETIC: written for this eval, not a real notice.

    cd backend && uv run python ../evals/golden/us/irs_sources_sought/make_notice.py

Writes next to this file:
  notice.pdf     4 pages: purpose/background, eligibility, scope (shall/must/should),
                 response format, submission instructions, evaluation criteria
  labels.json    the hand-labelled requirements (text, page, type) + eligibility fields
  recorded.json  the recorded extractor answer replayed by FakeLLM (one batch); regenerate
                 from a live run (BIDRADAR_LIVE_EVAL=1) once a key is available
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
LINE_WIDTH = 92
FONT_SIZE = 10
LEADING = 14
MARGIN = 72

# (heading, [(kind, text)]) per page; kind = "p" prose, or a requirement type
PAGES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "SOURCES SOUGHT NOTICE 2032H5-26-SS-0042",
        [
            ("p", "Department of the Treasury - Internal Revenue Service (IRS), Office of the Chief Procurement Officer."),
            ("p", "Title: Enterprise Cloud Migration and Managed Services. NAICS 541512. PSC DA01."),
            ("p", "1. Purpose. This is a Sources Sought notice issued for market research purposes only. It is not a request for proposal, quotation or offer, and the Government will not pay for information received."),
            ("p", "2. Background. The IRS operates approximately 400 workloads in the Kansas City and Ogden data centers that support taxpayer services and internal operations. The IRS intends to move these workloads to a FedRAMP authorized cloud and to obtain managed operations for them."),
            ("p", "3. Eligibility."),
            ("eligibility", "This requirement is being considered as a total small business set-aside under NAICS 541512 with a size standard of $34 million."),
            ("eligibility", "Respondents must be registered and active in SAM.gov at the time of response."),
            ("eligibility", "Respondents must hold an active FedRAMP Moderate authorization or be a partner of a FedRAMP Moderate authorized cloud service provider."),
        ],
    ),
    (
        "4. SCOPE OF WORK",
        [
            ("p", "The anticipated scope covers migration, operations and transition of the workloads described in section 2."),
            ("shall", "4.1 The contractor shall migrate approximately 400 on-premises workloads from the Kansas City and Ogden data centers to a FedRAMP Moderate cloud environment within 24 months of award."),
            ("shall", "4.2 The contractor shall provide 24x7 managed operations including monitoring, patching and incident response for all migrated workloads."),
            ("shall", "4.3 The contractor shall develop a migration wave plan within 45 calendar days of award."),
            ("shall", "4.4 The contractor shall maintain a Recovery Time Objective of four hours and a Recovery Point Objective of one hour for Tier 1 applications."),
            ("must", "4.5 The contractor must ensure that all personnel with access to Federal Tax Information complete IRS Publication 1075 background investigations before access is granted."),
            ("should", "4.6 The contractor should propose an approach for FinOps cost optimization and monthly cost reporting."),
            ("shall", "4.7 The contractor shall provide a transition-out plan 90 days before the end of the contract."),
        ],
    ),
    (
        "5. RESPONSE FORMAT",
        [
            ("format", "Responses shall not exceed 10 pages, excluding the cover page."),
            ("format", "Use 12-point Times New Roman font with one-inch margins on letter-size paper."),
            ("format", "Submit the capability statement as a single PDF file."),
            ("format", "File names shall follow the pattern CompanyName_IRS_SS_0042.pdf."),
            ("submission", "Include the company name, UEI, CAGE code, business size and socio-economic status on the cover page."),
            ("p", "6. Submission Instructions."),
            ("submission", "Responses must be emailed to the Contracting Officer at market.research@irs.example.gov no later than 2:00 PM Eastern Time on October 30, 2026."),
            ("submission", "The email subject line must read 'Sources Sought 2032H5-26-SS-0042 - Company Name'."),
            ("submission", "Questions must be submitted in writing by October 16, 2026; telephone inquiries will not be accepted."),
        ],
    ),
    (
        "7. EVALUATION OF RESPONSES",
        [
            ("p", "The Government will review capability statements to determine whether a small business set-aside is appropriate."),
            ("evaluation", "Responses will be reviewed for demonstrated experience migrating at least 200 workloads for a federal agency within the past three years."),
            ("evaluation", "The Government will assess the relevance of past performance in FedRAMP Moderate environments handling Federal Tax Information."),
            ("evaluation", "The Government will consider the respondent's ability to provide cleared staff in Kansas City, Missouri and Ogden, Utah."),
            ("should", "Capability statements should identify any teaming or subcontracting arrangements and the percentage of work to be performed by the small business prime."),
            ("p", "8. Disclaimer. This notice does not obligate the Government to award a contract. All information is to be provided at no cost."),
        ],
    ),
]

VOLUMES = {
    "eligibility": "Cover Page",
    "format": "Capability Statement",
    "submission": "Cover Page",
    "evaluation": "Capability Statement",
    "shall": "Technical",
    "must": "Technical",
    "should": "Technical",
}


def write_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for heading, items in PAGES:
        page = doc.new_page()
        y = MARGIN
        page.insert_text((MARGIN, y), heading, fontsize=13, fontname="helv")
        y += LEADING * 1.5
        for _, text in items:
            for line in textwrap.wrap(text, LINE_WIDTH):
                page.insert_text((MARGIN, y), line, fontsize=FONT_SIZE, fontname="helv")
                y += LEADING
            y += LEADING // 2
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def labels() -> dict[str, object]:
    rows = []
    for page_no, (_, items) in enumerate(PAGES, start=1):
        for kind, text in items:
            if kind == "p":
                continue
            rows.append({"id": f"L-{len(rows) + 1:02d}", "text": text, "page": page_no, "type": kind})
    return {
        "source": "synthetic IRS sources-sought pattern (evals/golden/us/irs_sources_sought)",
        "notice_id": "2032H5-26-SS-0042",
        "region": "us",
        "requirements": rows,
        "eligibility": {
            "naics": "541512",
            "set_aside": "small_business",
            "size_standard_usd": 34_000_000,
            "sam_registration_required": True,
            "fedramp_level": "Moderate",
        },
    }


# The recorded extractor answer replayed by FakeLLM. Deliberately NOT a copy of the
# labels: a verbatim replay would make the pass bars a tautology. It is what a good run
# looks like -- the requirements restated in the extractor's own words (so the fuzzy
# scorer is exercised), one plausible miss, one grounded over-extraction, one repeat and
# two ungrounded items the citation validator must drop. Each entry is
# (label id or None, restated text, page, quote copied from that page, type when there
# is no label to take it from).
PREDICTIONS: list[tuple[str | None, str, int, str, str | None]] = [
    ("L-01", "The requirement is contemplated as a total small business set-aside under NAICS 541512 with a size standard of $34 million.", 1, "total small business set-aside under NAICS 541512 with a size standard of $34 million", None),
    ("L-02", "Respondents must have an active SAM.gov registration at the time of response.", 1, "Respondents must be registered and active in SAM.gov at the time of response.", None),
    ("L-03", "Respondents must hold an active FedRAMP Moderate authorization, or partner with a FedRAMP Moderate authorized cloud service provider.", 1, "Respondents must hold an active FedRAMP Moderate authorization or be a partner of a FedRAMP Moderate authorized cloud service provider.", None),
    ("L-04", "Migrate approximately 400 on-premises workloads from the Kansas City and Ogden data centers to a FedRAMP Moderate cloud environment within 24 months of award.", 2, "The contractor shall migrate approximately 400 on-premises workloads from the Kansas City and Ogden data centers", None),
    ("L-05", "Provide 24x7 managed operations including monitoring, patching and incident response for all migrated workloads.", 2, "shall provide 24x7 managed operations including monitoring, patching and incident response", None),
    ("L-06", "Develop a migration wave plan within 45 calendar days of award.", 2, "shall develop a migration wave plan within 45 calendar days of award", None),
    ("L-07", "Maintain a Recovery Time Objective of four hours and a Recovery Point Objective of one hour for Tier 1 applications.", 2, "Recovery Time Objective of four hours and a Recovery Point Objective of one hour for Tier 1 applications", None),
    ("L-08", "Ensure all personnel with access to Federal Tax Information complete IRS Publication 1075 background investigations before access is granted.", 2, "all personnel with access to Federal Tax Information complete IRS Publication 1075 background investigations", None),
    # L-09 (4.6 FinOps cost optimization) is missed on purpose: recall must clear 90% with
    # a real gap, not because the answer was copied from the labels.
    ("L-10", "Provide a transition-out plan 90 days before the end of the contract.", 2, "shall provide a transition-out plan 90 days before the end of the contract", None),
    ("L-11", "Responses may not exceed 10 pages, excluding the cover page.", 3, "Responses shall not exceed 10 pages, excluding the cover page.", None),
    ("L-12", "Use 12-point Times New Roman font with one-inch margins on letter-size paper.", 3, "12-point Times New Roman font with one-inch margins on letter-size paper", None),
    ("L-13", "Submit the capability statement as a single PDF file.", 3, "Submit the capability statement as a single PDF file.", None),
    ("L-14", "File names must follow the pattern CompanyName_IRS_SS_0042.pdf.", 3, "File names shall follow the pattern CompanyName_IRS_SS_0042.pdf.", None),
    ("L-15", "Include the company name, UEI, CAGE code, business size and socio-economic status on the cover page.", 3, "company name, UEI, CAGE code, business size and socio-economic status on the cover page", None),
    ("L-16", "Email responses to the Contracting Officer at market.research@irs.example.gov no later than 2:00 PM Eastern Time on October 30, 2026.", 3, "emailed to the Contracting Officer at market.research@irs.example.gov no later than 2:00 PM Eastern Time on October 30, 2026", None),
    ("L-17", "The email subject line must read 'Sources Sought 2032H5-26-SS-0042 - Company Name'.", 3, "The email subject line must read 'Sources Sought 2032H5-26-SS-0042 - Company Name'.", None),
    ("L-18", "Submit questions in writing by October 16, 2026; telephone inquiries will not be accepted.", 3, "Questions must be submitted in writing by October 16, 2026; telephone inquiries will not be accepted.", None),
    ("L-19", "Responses are reviewed for demonstrated experience migrating at least 200 workloads for a federal agency within the past three years.", 4, "demonstrated experience migrating at least 200 workloads for a federal agency within the past three years", None),
    ("L-20", "The Government will assess the relevance of past performance in FedRAMP Moderate environments handling Federal Tax Information.", 4, "assess the relevance of past performance in FedRAMP Moderate environments handling Federal Tax Information", None),
    ("L-21", "The Government will consider the respondent's ability to provide cleared staff in Kansas City, Missouri and Ogden, Utah.", 4, "ability to provide cleared staff in Kansas City, Missouri and Ogden, Utah", None),
    ("L-22", "Capability statements should identify teaming or subcontracting arrangements and the percentage of work performed by the small business prime.", 4, "identify any teaming or subcontracting arrangements and the percentage of work to be performed by the small business prime", None),
    # grounded but not a requirement: a real quote from the disclaimer. It survives the
    # validator and costs precision, which is what the 85% bar is for.
    (None, "All information is to be provided at no cost to the Government.", 4, "All information is to be provided at no cost.", "submission"),
    # the same obligation stated twice (summary + body); dedupe must keep one.
    (None, "Provide 24x7 managed operations, including monitoring, patching and incident response, for all migrated workloads.", 2, "24x7 managed operations including monitoring, patching and incident response", "shall"),
    # ungrounded: a page the batch does not hold. The validator must reject it.
    (None, "The contractor shall staff a program management office in Washington, DC.", 7, "program management office in Washington, DC", "shall"),
    # ungrounded: an invented quote. The validator must reject it.
    (None, "The contractor shall provide onsite staff at all IRS facilities nationwide.", 2, "the contractor shall provide onsite staff at all IRS facilities nationwide", "shall"),
]
EXPECTED = {"rejected": 2, "duplicates_removed": 1, "missed_labels": ["L-09"]}


def recorded(label_rows: list[dict[str, object]]) -> dict[str, object]:
    """One batch answer in the extractor's ExtractionOutput shape."""
    types = {str(row["id"]): str(row["type"]) for row in label_rows}
    known = set(types)
    for label_id, *_ in PREDICTIONS:
        assert label_id is None or label_id in known, label_id
    missed = sorted(known - {lid for lid, *_ in PREDICTIONS if lid})
    assert missed == EXPECTED["missed_labels"], missed
    requirements = []
    for label_id, text, page, quote, override in PREDICTIONS:
        kind = types[label_id] if label_id else str(override)
        requirements.append(
            {
                "text": text,
                "page": page,
                "type": kind,
                "volume": VOLUMES[kind],
                "quote": quote,
                "confidence": 0.9,
            }
        )
    return {
        "model_class": "opus",
        "expected": EXPECTED,
        "batches": [{"requirements": requirements}],
    }


if __name__ == "__main__":
    total = sum(len(t) for _, items in PAGES for _, t in items)
    assert total < 12_000, "the notice must fit one extractor batch"
    write_pdf(HERE / "notice.pdf")
    label_data = labels()
    (HERE / "labels.json").write_text(json.dumps(label_data, indent=2) + "\n")
    rows = label_data["requirements"]
    assert isinstance(rows, list)
    (HERE / "recorded.json").write_text(json.dumps(recorded(rows), indent=2) + "\n")
    print(f"wrote notice.pdf ({len(PAGES)} pages), {len(rows)} labels, recorded answer")
