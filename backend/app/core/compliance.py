"""Pure compliance-matrix logic (SPEC 8 agent 3): requirement -> proposal section,
the solicitation's format rules (page limits, fonts, file naming, portal, copies) and the
region's submission checklist (US forms / India EMD-BG-DSC items). No I/O, no LLM.

    assignment = section_for(req)            # .section, .reason, .ambiguous
    rules = extract_format_rules(reqs)       # FormatRules
    checklist = build_checklist(ctx, reqs)   # list[ChecklistItem]

`ambiguous` assignments are the only ones app.agents.matrix sends to the classification
model; everything else is decided here so the matrix is reproducible and cheap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.money import format_money

# --- proposal sections ----------------------------------------------------------------

SECTION_COVER = "Cover Letter"
SECTION_EXEC = "Executive Summary"
SECTION_TECHNICAL = "Technical Approach"
SECTION_MANAGEMENT = "Management Plan"
SECTION_STAFFING = "Staffing and Key Personnel"
SECTION_PAST_PERFORMANCE = "Past Performance"
SECTION_PRICE = "Price"
SECTION_ELIGIBILITY = "Eligibility and Certifications"
SECTION_SUBMISSION = "Submission Package"

SECTIONS: tuple[str, ...] = (
    SECTION_COVER,
    SECTION_EXEC,
    SECTION_TECHNICAL,
    SECTION_MANAGEMENT,
    SECTION_STAFFING,
    SECTION_PAST_PERFORMANCE,
    SECTION_PRICE,
    SECTION_ELIGIBILITY,
    SECTION_SUBMISSION,
)

ITEM_STATUSES: tuple[str, ...] = ("open", "drafted", "reviewed", "done", "na")
STATUS_OPEN = "open"

# artifact kinds stored on pursuit_artifacts (SPEC 8: outputs are stored and versioned)
ARTIFACT_FORMAT_RULES = "format_rules"
ARTIFACT_CHECKLIST = "checklist"
ARTIFACT_PACKET = "packet"
ARTIFACT_SCORECARD = "scorecard"
ARTIFACT_OUTLINE = "outline"
ARTIFACT_PRICING_TEMPLATE = "pricing_template"
ARTIFACT_KINDS: tuple[str, ...] = (
    ARTIFACT_FORMAT_RULES,
    ARTIFACT_CHECKLIST,
    ARTIFACT_PACKET,
    ARTIFACT_SCORECARD,
    ARTIFACT_OUTLINE,
    ARTIFACT_PRICING_TEMPLATE,
)
CREATED_BY_AGENT = "agent"
CREATED_BY_USER = "user"


@dataclass(frozen=True, slots=True)
class Req:
    """The part of a requirement row the matrix reasons about."""

    req_id: str
    text: str
    type: str
    volume: str | None = None


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _has(haystack: str, needle: str) -> bool:
    """Whole-word containment so 'bg' does not match 'debugging'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


# --- requirement -> section -----------------------------------------------------------

# A volume named by the solicitation wins; first match in order.
VOLUME_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cover letter", "cover page", "transmittal", "covering letter"), SECTION_COVER),
    (("executive summary", "summary volume"), SECTION_EXEC),
    (("past performance", "corporate experience", "references"), SECTION_PAST_PERFORMANCE),
    (("staffing", "personnel", "resume", "resumes", "key staff"), SECTION_STAFFING),
    (("price", "pricing", "cost", "financial", "commercial", "boq"), SECTION_PRICE),
    (("management", "project plan"), SECTION_MANAGEMENT),
    (("technical", "capability statement", "solution"), SECTION_TECHNICAL),
    (("eligibility", "qualification", "pre-qualification"), SECTION_ELIGIBILITY),
    (("submission", "forms", "annexure", "checklist"), SECTION_SUBMISSION),
)

# Text keywords for obligations and evaluation criteria with no volume; first match wins,
# so the more specific phrases come first.
KEYWORD_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "past performance",
            "prior contract",
            "previous experience",
            "similar projects",
            "experience migrating",
            "cpars",
            "reference contracts",
        ),
        SECTION_PAST_PERFORMANCE,
    ),
    (
        (
            "key personnel",
            "personnel",
            "staffing",
            "resume",
            "labor category",
            "labour category",
            "background investigation",
            "security clearance",
            "cleared staff",
        ),
        SECTION_STAFFING,
    ),
    (
        (
            "price",
            "pricing",
            "cost proposal",
            "cost volume",
            "invoice",
            "billing",
            "rate card",
            "man-month",
            "unit rate",
            "gst",
            "emd",
            "bid security",
        ),
        SECTION_PRICE,
    ),
    (
        (
            "schedule",
            "milestone",
            "risk",
            "governance",
            "quality assurance",
            "transition",
            "reporting",
            "subcontract",
            "teaming",
            "escalation",
            "service level",
            "sla",
            "programme management",
            "program management",
        ),
        SECTION_MANAGEMENT,
    ),
    (
        (
            "architecture",
            "security",
            "monitoring",
            "patching",
            "incident response",
            "recovery time objective",
            "recovery point objective",
            "solution",
            "design",
            "migrate",
            "migration",
        ),
        SECTION_TECHNICAL,
    ),
)

TYPE_SECTIONS: dict[str, str] = {
    "eligibility": SECTION_ELIGIBILITY,
    "format": SECTION_SUBMISSION,
    "submission": SECTION_SUBMISSION,
}
OBLIGATION_TYPES: frozenset[str] = frozenset({"shall", "must", "should"})
DEFAULT_SECTION = SECTION_TECHNICAL

REASON_VOLUME = "volume"
REASON_TYPE = "type"
REASON_KEYWORD = "keyword"
REASON_DEFAULT = "default"
REASON_MODEL = "model"


@dataclass(frozen=True, slots=True)
class Assignment:
    req_id: str
    section: str
    reason: str
    ambiguous: bool = False


def _volume_section(volume: str | None) -> str | None:
    text = _norm(volume)
    if not text:
        return None
    for aliases, section in VOLUME_MAP:
        if any(alias in text for alias in aliases):
            return section
    return None


def _keyword_section(text: str) -> str | None:
    lowered = _norm(text)
    for keywords, section in KEYWORD_MAP:
        if any(keyword in lowered for keyword in keywords):
            return section
    return None


def section_for(req: Req) -> Assignment:
    """Map one requirement to a proposal section. `ambiguous` marks the obligations and
    evaluation criteria that neither a volume nor a keyword decided: the classification
    model is asked about those, and only those."""
    volume = _volume_section(req.volume)
    if volume is not None:
        return Assignment(req.req_id, volume, REASON_VOLUME)
    by_type = TYPE_SECTIONS.get(req.type)
    if by_type is not None:
        return Assignment(req.req_id, by_type, REASON_TYPE)
    keyword = _keyword_section(req.text)
    if keyword is not None:
        return Assignment(req.req_id, keyword, REASON_KEYWORD)
    ambiguous = req.type in OBLIGATION_TYPES or req.type == "evaluation"
    return Assignment(req.req_id, DEFAULT_SECTION, REASON_DEFAULT, ambiguous=ambiguous)


def assign_sections(reqs: Sequence[Req]) -> list[Assignment]:
    return [section_for(req) for req in reqs]


def apply_model_sections(
    assignments: Sequence[Assignment], chosen: dict[str, str]
) -> list[Assignment]:
    """Overlay the classification model's answers on the ambiguous assignments. A section
    the model invented, or an answer for a requirement that was never ambiguous, is
    ignored: the heuristic stands."""
    out: list[Assignment] = []
    for item in assignments:
        section = chosen.get(item.req_id)
        if item.ambiguous and section in SECTIONS:
            out.append(Assignment(item.req_id, str(section), REASON_MODEL))
        else:
            out.append(item)
    return out


def section_counts(assignments: Iterable[Assignment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in assignments:
        counts[item.section] = counts.get(item.section, 0) + 1
    return counts


# --- format rules ---------------------------------------------------------------------

_PAGE_LIMIT_RES = (
    re.compile(r"(?:not exceed|no more than|maximum of|limited to)\s+(\d{1,3})\s+pages"),
    re.compile(r"(\d{1,3})[- ]page (?:limit|maximum)"),
    re.compile(r"page limit (?:of|is)\s+(\d{1,3})"),
)
_COPIES_RE = re.compile(
    r"(\d{1,2})\s+(?:hard|printed|paper|bound|complete|identical)?\s*(?:copies|sets)"
)
# A font name is one to three capitalised words ("Times New Roman", "Arial"); the size may
# come before it ("12-point Times New Roman") or after it ("Arial 11 point").
_FONT_RES = (
    re.compile(r"(\d{1,2})[- ]point\s+([A-Z][\w]*(?:\s+[A-Z][\w]*){0,2})"),
    re.compile(
        r"([A-Z][\w]*(?:\s+[A-Z][\w]*){0,2})\s+(?:font\s+)?(?:at\s+)?(\d{1,2})\s*(?:pt|point)"
    ),
)
# words that start a sentence and would otherwise be read as a font name
_FONT_STOPWORDS = frozenset({"the", "use", "set", "with", "in", "a", "all", "text", "font"})
_MARGIN_RE = re.compile(r"((?:[\d.]+|one|half)[-\s]?(?:inch|\"|cm|mm)\s+margins)")
_FILE_TYPE_RE = re.compile(r"(?<![a-z0-9])(pdf|docx?|xlsx?|pptx?|zip)(?![a-z0-9])")
_FILE_NAMING_RES = (
    re.compile(r"(?:pattern|convention|format)\s+([A-Za-z0-9_\-\[\]<>{}]+\.[A-Za-z]{2,5})"),
    re.compile(r"named?\s+([A-Za-z0-9_\-\[\]<>{}]+\.[A-Za-z]{2,5})"),
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[\w]")

PORTALS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("sam.gov", "system for award management"), "SAM.gov"),
    (("grants.gov",), "Grants.gov"),
    (("gem.gov.in", "gem portal", "gem bid"), "GeM"),
    (("eprocure.gov.in", "cppp", "central public procurement portal"), "CPPP (eprocure.gov.in)"),
    (("gepnic",), "GePNIC"),
)
METHOD_EMAIL = "email"
METHOD_PORTAL = "portal"

_RULE_TYPES: frozenset[str] = frozenset({"format", "submission"})


class FormatRules(BaseModel):
    """What the solicitation says about the shape of the response. Every field is None /
    empty unless a requirement actually stated it -- nothing is guessed."""

    page_limit: int | None = None
    font: str | None = None
    font_size_pt: int | None = None
    margins: str | None = None
    file_types: list[str] = Field(default_factory=list)
    file_naming: str | None = None
    copies: int | None = None
    portal: str | None = None
    submission_method: str | None = None  # email | portal
    email: str | None = None
    # field name -> the req_id the value was read from (the clickable citation)
    sources: dict[str, str] = Field(default_factory=dict)


def _title(value: str) -> str:
    return " ".join(part if part.isupper() else part.capitalize() for part in value.split())


def extract_format_rules(reqs: Sequence[Req], *, portal_hint: str | None = None) -> FormatRules:
    """Read the page limit, font, margins, file types/naming, copies and portal out of the
    format and submission requirements. `portal_hint` (the opportunity's source) is used
    only when no requirement names a portal."""
    rules = FormatRules()
    candidates = [r for r in reqs if r.type in _RULE_TYPES] or list(reqs)

    def note(field: str, req: Req) -> None:
        rules.sources.setdefault(field, req.req_id)

    for req in candidates:
        lowered = _norm(req.text)
        if rules.page_limit is None:
            for pattern in _PAGE_LIMIT_RES:
                found = pattern.search(lowered)
                if found:
                    rules.page_limit = int(found.group(1))
                    note("page_limit", req)
                    break
        if rules.font is None:
            for index, pattern in enumerate(_FONT_RES):
                found = pattern.search(req.text)
                if found:
                    name, size = (
                        (found.group(2), found.group(1))
                        if index == 0
                        else (found.group(1), found.group(2))
                    )
                    cleaned = name.strip()
                    if cleaned.split()[0].lower() in _FONT_STOPWORDS:
                        continue
                    rules.font = _title(cleaned)
                    rules.font_size_pt = int(size)
                    note("font", req)
                    note("font_size_pt", req)
                    break
        if rules.margins is None:
            found = _MARGIN_RE.search(lowered)
            if found:
                rules.margins = found.group(1)
                note("margins", req)
        if rules.copies is None:
            found = _COPIES_RE.search(lowered)
            if found:
                rules.copies = int(found.group(1))
                note("copies", req)
        if rules.file_naming is None:
            for pattern in _FILE_NAMING_RES:
                found = pattern.search(req.text)
                if found:
                    rules.file_naming = found.group(1)
                    note("file_naming", req)
                    break
        for match in _FILE_TYPE_RE.finditer(lowered):
            kind = match.group(1).upper()
            if kind not in rules.file_types:
                rules.file_types.append(kind)
                note("file_types", req)
        if rules.email is None:
            found = _EMAIL_RE.search(req.text)
            if found:
                rules.email = found.group(0)
                note("email", req)
        if rules.portal is None:
            for aliases, name in PORTALS:
                if any(alias in lowered for alias in aliases):
                    rules.portal = name
                    note("portal", req)
                    break

    if rules.portal is None and portal_hint:
        rules.portal = portal_hint
    if rules.email is not None:
        rules.submission_method = METHOD_EMAIL
    elif rules.portal is not None:
        rules.submission_method = METHOD_PORTAL
    return rules


# --- submission checklist -------------------------------------------------------------


class ChecklistItem(BaseModel):
    key: str
    label: str
    category: str  # registration | form | certificate | signature | payment | attachment
    required: bool
    note: str | None = None
    source_req_ids: list[str] = Field(default_factory=list)


class ChecklistContext(BaseModel):
    """Everything outside the requirements that shapes the checklist."""

    region: str  # us | in
    notice_type: str | None = None
    source_id: str | None = None
    set_aside: str | None = None
    currency: str = "USD"
    emd_amount: Decimal | None = None
    tender_fee: Decimal | None = None


CAT_REGISTRATION = "registration"
CAT_FORM = "form"
CAT_CERTIFICATE = "certificate"
CAT_SIGNATURE = "signature"
CAT_PAYMENT = "payment"
CAT_ATTACHMENT = "attachment"

# key -> phrases that mean a requirement is talking about that checklist item
ITEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sam_registration": ("sam.gov", "sam registration", "system for award management"),
    "reps_certs": (
        "representations and certifications",
        "reps and certs",
        "52.212-3",
        "52.204-8",
    ),
    "sf_33": ("sf-33", "sf 33", "standard form 33"),
    "sf_1449": ("sf-1449", "sf 1449", "standard form 1449"),
    "set_aside_eligibility": ("set-aside", "set aside", "size standard"),
    "capability_statement": ("capability statement",),
    "dsc": ("dsc", "digital signature", "class 3"),
    "dsc_signed_covers": ("digitally signed", "dsc-signed", "signed cover"),
    "emd": ("emd", "earnest money"),
    "bank_guarantee": ("bank guarantee", "bg"),
    "tender_fee": ("tender fee", "tender document fee", "processing fee"),
    "turnover_certificate": ("turnover",),
    "affidavit": ("affidavit", "blacklist", "blacklisted", "debarred", "debarment"),
    "power_of_attorney": ("power of attorney", "board resolution", "authorisation letter"),
    "pan_gst": ("pan", "gst", "gstin"),
    "gem_seller": ("gem seller", "gem.gov.in", "gem bid", "seller id"),
    "cppp_enrolment": ("cppp", "gepnic", "eprocure.gov.in", "bidder enrolment"),
    "msme_udyam": ("udyam", "msme"),
}
# items a requirement can promote from "check whether it applies" to "required"
UPGRADABLE: frozenset[str] = frozenset(
    {
        "sf_33",
        "sf_1449",
        "set_aside_eligibility",
        "capability_statement",
        "bank_guarantee",
        "emd",
        "tender_fee",
        "gem_seller",
        "cppp_enrolment",
    }
)

US_FORM_NOTICE_TYPES: frozenset[str] = frozenset({"rfp", "ifb", "combined"})
US_QUOTE_NOTICE_TYPES: frozenset[str] = frozenset({"rfq"})
US_MARKET_RESEARCH_TYPES: frozenset[str] = frozenset({"rfi", "sources_sought", "eoi"})
GEM_SOURCES = ("gem",)
CPPP_SOURCES = ("cppp", "gepnic", "eprocure", "nicgep")


def citations(reqs: Sequence[Req]) -> dict[str, list[str]]:
    """checklist key -> the req_ids that mention it."""
    found: dict[str, list[str]] = {}
    for req in reqs:
        lowered = _norm(req.text)
        for key, keywords in ITEM_KEYWORDS.items():
            if any(_has(lowered, keyword) for keyword in keywords):
                found.setdefault(key, []).append(req.req_id)
    return found


def _item(
    cited: dict[str, list[str]],
    key: str,
    label: str,
    category: str,
    *,
    required: bool,
    note: str | None = None,
) -> ChecklistItem:
    sources = cited.get(key, [])
    if sources and key in UPGRADABLE:
        required = True
    return ChecklistItem(
        key=key,
        label=label,
        category=category,
        required=required,
        note=note,
        source_req_ids=sources,
    )


def _amount(value: Decimal | None, currency: str) -> str | None:
    return None if value is None else format_money(Decimal(value), currency)


def _us_items(ctx: ChecklistContext, cited: dict[str, list[str]]) -> list[ChecklistItem]:
    notice = (ctx.notice_type or "").lower()
    sf33 = notice in US_FORM_NOTICE_TYPES
    sf1449 = notice in US_QUOTE_NOTICE_TYPES
    unknown_form = not sf33 and not sf1449
    form_note = (
        "Confirm which standard form this solicitation uses before signing."
        if unknown_form
        else None
    )
    items = [
        _item(
            cited,
            "sam_registration",
            "SAM.gov registration active with UEI and CAGE code",
            CAT_REGISTRATION,
            required=True,
        ),
        _item(
            cited,
            "reps_certs",
            "Representations and Certifications current in SAM (FAR 52.204-8 / 52.212-3)",
            CAT_FORM,
            required=True,
        ),
        _item(
            cited,
            "sf_33",
            "SF-33 Solicitation, Offer and Award signed by an authorised representative",
            CAT_FORM,
            required=sf33,
            note=form_note,
        ),
        _item(
            cited,
            "sf_1449",
            "SF-1449 Solicitation/Contract/Order for Commercial Items signed",
            CAT_FORM,
            required=sf1449,
            note=form_note,
        ),
        _item(
            cited,
            "set_aside_eligibility",
            "Set-aside eligibility confirmed (size standard and socio-economic certifications)",
            CAT_CERTIFICATE,
            required=bool(ctx.set_aside),
            note=None if not ctx.set_aside else f"Notice is a {ctx.set_aside} set-aside.",
        ),
        _item(
            cited,
            "capability_statement",
            "Capability statement covering the information the notice asks for",
            CAT_ATTACHMENT,
            required=notice in US_MARKET_RESEARCH_TYPES,
        ),
    ]
    return items


def _in_items(ctx: ChecklistContext, cited: dict[str, list[str]]) -> list[ChecklistItem]:
    source = (ctx.source_id or "").lower()
    emd = _amount(ctx.emd_amount, ctx.currency)
    fee = _amount(ctx.tender_fee, ctx.currency)
    return [
        _item(
            cited,
            "dsc",
            "Class 3 digital signature certificate (signing) valid and mapped to the portal",
            CAT_SIGNATURE,
            required=True,
            note="Never store the DSC private key in BidRadar; the signer uses their token.",
        ),
        _item(
            cited,
            "dsc_signed_covers",
            "Technical and financial covers digitally signed with the Class 3 DSC before upload",
            CAT_SIGNATURE,
            required=True,
        ),
        _item(
            cited,
            "emd",
            "Earnest money deposit paid or a bank guarantee furnished",
            CAT_PAYMENT,
            required=ctx.emd_amount is not None,
            note=None if emd is None else f"EMD stated on the notice: {emd}.",
        ),
        _item(
            cited,
            "bank_guarantee",
            "Bank guarantee in the prescribed format, if the EMD is furnished as a BG",
            CAT_PAYMENT,
            required=False,
            note="Check the validity period demanded beyond bid validity.",
        ),
        _item(
            cited,
            "tender_fee",
            "Tender document fee paid",
            CAT_PAYMENT,
            required=ctx.tender_fee is not None,
            note=None if fee is None else f"Tender fee stated on the notice: {fee}.",
        ),
        _item(
            cited,
            "turnover_certificate",
            "Chartered Accountant certified turnover statement for the last three financial years",
            CAT_CERTIFICATE,
            required=True,
        ),
        _item(
            cited,
            "affidavit",
            "Non-blacklisting / non-debarment affidavit on stamp paper, notarised",
            CAT_FORM,
            required=True,
        ),
        _item(
            cited,
            "power_of_attorney",
            "Power of attorney or board resolution authorising the signatory",
            CAT_FORM,
            required=True,
        ),
        _item(
            cited,
            "pan_gst",
            "PAN and GST registration certificates",
            CAT_CERTIFICATE,
            required=True,
        ),
        _item(
            cited,
            "gem_seller",
            "GeM seller account active with the seller id and the category approved",
            CAT_REGISTRATION,
            required=any(token in source for token in GEM_SOURCES),
        ),
        _item(
            cited,
            "cppp_enrolment",
            "CPPP / GePNIC bidder enrolment active with the DSC mapped to the profile",
            CAT_REGISTRATION,
            required=any(token in source for token in CPPP_SOURCES),
        ),
        _item(
            cited,
            "msme_udyam",
            "Udyam (MSME) registration attached if EMD or tender-fee exemption is claimed",
            CAT_CERTIFICATE,
            required=False,
        ),
    ]


def build_checklist(ctx: ChecklistContext, reqs: Sequence[Req]) -> list[ChecklistItem]:
    """The region's submission checklist, with each item pointing at the requirements that
    mention it. Region decides the list; requirements only add citations and can promote a
    conditional item to required."""
    cited = citations(reqs)
    if ctx.region == "us":
        return _us_items(ctx, cited)
    if ctx.region == "in":
        return _in_items(ctx, cited)
    raise ValueError(f"unknown region {ctx.region!r}")
