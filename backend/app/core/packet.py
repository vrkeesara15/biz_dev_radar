"""Submission packet (SPEC 8, 1): what a human uploads where, the portal link, the
signatures / DSC steps and the final deadline in both time zones. Pure.

    packet = build_packet(ctx, rules, checklist)

BidRadar never submits. Nothing here performs, schedules or authorises a submission: the
packet is a set of instructions for the person who logs in to the portal themselves, and
`NEVER_SUBMITS` says so on every response.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.compliance import ChecklistItem, FormatRules
from app.core.disclaimers import VERIFY_ON_PORTAL
from app.core.display_time import TzDateOut, tz_fields_or_none

NEVER_SUBMITS = (
    f"BidRadar never submits a bid. A person signs in to the portal and submits; {VERIFY_ON_PORTAL}"
)
SAM_LOGIN_NOTE = (
    "Submit from SAM.gov signed in with your own entity's account. BidRadar never stores "
    "SAM.gov credentials and never logs in for you."
)
REGION_US = "us"
REGION_IN = "in"
DEFAULT_FORMATS = ("PDF",)

# checklist items that are a file the bidder uploads, as opposed to portal-side state
# (registrations), a payment (EMD, tender fee) or a signature step.
UPLOAD_ITEMS: dict[str, tuple[str, ...]] = {
    REGION_US: ("sf_33", "sf_1449", "capability_statement"),
    REGION_IN: (
        "turnover_certificate",
        "affidavit",
        "power_of_attorney",
        "pan_gst",
        "bank_guarantee",
        "msme_udyam",
    ),
}
MARKET_RESEARCH_TYPES: frozenset[str] = frozenset({"rfi", "sources_sought", "eoi"})

DSC_STEPS: tuple[str, ...] = (
    "Insert the Class 3 signing DSC token and sign in to the portal with the enrolled "
    "bidder account (the signer does this personally).",
    "Check that the DSC is mapped to the bidder profile and has not expired.",
    "Digitally sign the technical cover with the DSC before uploading it.",
    "Digitally sign the financial cover / BoQ with the DSC before uploading it.",
    "Confirm the portal lists both covers as signed, then keep the bid acknowledgement.",
)


class UploadStep(BaseModel):
    order: int
    label: str
    destination: str
    file_name: str | None = None
    formats: list[str] = Field(default_factory=list)
    note: str | None = None
    source_req_ids: list[str] = Field(default_factory=list)


class SignatureStep(BaseModel):
    key: str
    label: str
    signed_by: str
    note: str | None = None


class PacketContext(BaseModel):
    """Everything outside the format rules and the checklist that shapes the packet."""

    region: str
    notice_type: str | None = None
    title: str | None = None
    solicitation_number: str | None = None
    portal_url: str | None = None
    buyer_tz: str = "UTC"
    user_tz: str | None = None
    response_due_at: datetime | None = None


class Packet(BaseModel):
    portal: str | None
    portal_url: str | None
    submission_method: str | None  # email | portal
    email: str | None
    upload_steps: list[UploadStep] = Field(default_factory=list)
    signatures: list[SignatureStep] = Field(default_factory=list)
    dsc_steps: list[str] = Field(default_factory=list)  # India only
    sam_login_note: str | None = None  # US only
    page_limit: int | None = None
    copies: int | None = None
    deadline: TzDateOut | None = None
    disclaimer: str = NEVER_SUBMITS


def _formats(rules: FormatRules) -> list[str]:
    return list(rules.file_types) if rules.file_types else list(DEFAULT_FORMATS)


def _file_name(rules: FormatRules, ctx: PacketContext) -> str | None:
    """The solicitation's naming rule with the obvious placeholder filled in, or None.
    The company name is deliberately left as a placeholder for the human to replace."""
    if not rules.file_naming:
        return None
    name = rules.file_naming
    if ctx.solicitation_number and "<solicitation>" in name.lower():
        name = name.replace("<solicitation>", ctx.solicitation_number)
    return name


def _destination(rules: FormatRules, ctx: PacketContext) -> str:
    if rules.submission_method == "email" and rules.email:
        return f"Email to {rules.email}"
    portal = rules.portal or ctx.portal_url
    return f"Upload on {portal}" if portal else "Upload on the buyer's portal"


def _response_label(ctx: PacketContext) -> str:
    notice = (ctx.notice_type or "").lower()
    if notice in MARKET_RESEARCH_TYPES:
        return "Capability statement / response"
    return "Proposal response"


def _upload_steps(
    ctx: PacketContext, rules: FormatRules, checklist: list[ChecklistItem]
) -> list[UploadStep]:
    default_destination = _destination(rules, ctx)
    formats = _formats(rules)
    steps: list[UploadStep] = []

    def add(
        label: str,
        *,
        destination: str | None = None,
        file_name: str | None = None,
        with_formats: bool = True,
        note: str | None = None,
        source_req_ids: list[str] | None = None,
    ) -> None:
        steps.append(
            UploadStep(
                order=len(steps) + 1,
                label=label,
                destination=destination or default_destination,
                file_name=file_name,
                formats=formats if with_formats else [],
                note=note,
                source_req_ids=source_req_ids or [],
            )
        )

    if ctx.region == REGION_IN:
        add("Technical bid cover", note="Digitally sign with the Class 3 DSC before upload.")
        add(
            "Financial bid cover / BoQ",
            note="Digitally sign with the Class 3 DSC before upload; use the financial "
            "cover slot only.",
        )
    else:
        add(
            _response_label(ctx),
            file_name=_file_name(rules, ctx),
            note=None
            if rules.page_limit is None
            else f"Keep within the {rules.page_limit}-page limit.",
        )

    uploadable = UPLOAD_ITEMS.get(ctx.region, ())
    for item in checklist:
        if item.key in uploadable and item.required:
            add(item.label, note=item.note, source_req_ids=list(item.source_req_ids))
    if rules.copies:
        add(
            f"{rules.copies} hard copies",
            destination="Deliver as the solicitation directs",
            with_formats=False,
            note="Hard copies are delivered by a person, not by BidRadar.",
            source_req_ids=[rules.sources["copies"]] if "copies" in rules.sources else [],
        )
    return steps


def _signatures(ctx: PacketContext, checklist: list[ChecklistItem]) -> list[SignatureStep]:
    by_key = {item.key: item for item in checklist}
    if ctx.region == REGION_IN:
        signatures = [
            SignatureStep(
                key="dsc_covers",
                label="Digitally sign every cover with the Class 3 DSC",
                signed_by="the DSC holder named on the certificate",
                note="The token stays with the signer; BidRadar never holds a private key.",
            )
        ]
        if "affidavit" in by_key:
            signatures.append(
                SignatureStep(
                    key="affidavit",
                    label="Notarised non-blacklisting affidavit on stamp paper",
                    signed_by="an authorised signatory, before a notary",
                )
            )
        if "power_of_attorney" in by_key:
            signatures.append(
                SignatureStep(
                    key="power_of_attorney",
                    label="Power of attorney or board resolution for the signatory",
                    signed_by="the board or an authorised director",
                )
            )
        return signatures
    signatures = [
        SignatureStep(
            key="offer_form",
            label="Sign the standard form (SF-33 block 30 / SF-1449 block 30A)",
            signed_by="an authorised representative of the offeror",
        )
    ]
    if by_key.get("reps_certs") is not None:
        signatures.append(
            SignatureStep(
                key="reps_certs",
                label="Certify the Representations and Certifications",
                signed_by="the entity administrator, in SAM.gov",
                note="Certified online in SAM, not uploaded with the response.",
            )
        )
    return signatures


def build_packet(ctx: PacketContext, rules: FormatRules, checklist: list[ChecklistItem]) -> Packet:
    """The submission packet. Read-only instructions for a human: no call is made and no
    submission is triggered anywhere in this module."""
    if ctx.region not in (REGION_US, REGION_IN):
        raise ValueError(f"unknown region {ctx.region!r}")
    return Packet(
        portal=rules.portal or ctx.portal_url,
        portal_url=ctx.portal_url,
        submission_method=rules.submission_method,
        email=rules.email,
        upload_steps=_upload_steps(ctx, rules, checklist),
        signatures=_signatures(ctx, checklist),
        dsc_steps=list(DSC_STEPS) if ctx.region == REGION_IN else [],
        sam_login_note=SAM_LOGIN_NOTE if ctx.region == REGION_US else None,
        page_limit=rules.page_limit,
        copies=rules.copies,
        deadline=tz_fields_or_none(ctx.response_due_at, ctx.buyer_tz, ctx.user_tz, with_year=True),
    )
