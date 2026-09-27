"""M5-14: the submission packet builder is pure and never submits anything."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from app.core import packet as packet_module
from app.core.compliance import (
    ChecklistContext,
    FormatRules,
    Req,
    build_checklist,
    extract_format_rules,
)
from app.core.packet import (
    DSC_STEPS,
    NEVER_SUBMITS,
    SAM_LOGIN_NOTE,
    Packet,
    PacketContext,
    build_packet,
)

DUE = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)

US_REQS = [
    Req("R-001", "Respondents must be registered and active in SAM.gov.", "eligibility"),
    Req("R-002", "Responses shall not exceed 10 pages, excluding the cover page.", "format"),
    Req("R-003", "File names shall follow the pattern CompanyName_IRS_SS_0042.pdf.", "format"),
    Req("R-004", "Submit the capability statement as a single PDF file.", "format"),
    Req(
        "R-005",
        "Responses must be emailed to market.research@irs.example.gov by 2:00 PM Eastern.",
        "submission",
    ),
]
IN_REQS = [
    Req("R-001", "Bidders must furnish EMD of INR 2,50,000 or a bank guarantee.", "eligibility"),
    Req("R-002", "Average annual turnover of INR 5 crore is required.", "eligibility"),
    Req("R-003", "Both covers must be signed with a Class 3 DSC on eprocure.gov.in.", "submission"),
    Req("R-004", "Upload the technical cover as a PDF.", "format"),
]


def _us_packet(due: datetime | None = DUE) -> Packet:
    rules = extract_format_rules(US_REQS)
    checklist = build_checklist(
        ChecklistContext(region="us", notice_type="sources_sought", source_id="sam_opps"), US_REQS
    )
    ctx = PacketContext(
        region="us",
        notice_type="sources_sought",
        title="Enterprise Cloud Migration",
        solicitation_number="2032H5-26-SS-0042",
        portal_url="https://sam.gov/opp/abc",
        buyer_tz="America/New_York",
        user_tz="Asia/Kolkata",
        response_due_at=due,
    )
    return build_packet(ctx, rules, checklist)


def test_us_packet_names_the_uploads_the_portal_and_the_sam_login_note() -> None:
    packet = _us_packet()
    # no requirement names a portal here, so the notice's own URL is the portal link
    assert packet.portal == "https://sam.gov/opp/abc" == packet.portal_url
    assert packet.submission_method == "email" and packet.email == "market.research@irs.example.gov"
    labels = [(s.order, s.label, s.destination) for s in packet.upload_steps]
    assert labels[0] == (
        1,
        "Capability statement / response",
        "Email to market.research@irs.example.gov",
    )
    first = packet.upload_steps[0]
    assert first.file_name == "CompanyName_IRS_SS_0042.pdf" and first.formats == ["PDF"]
    assert "10-page limit" in (first.note or "")
    # the required capability statement is its own upload, citing the requirement
    assert [s.label for s in packet.upload_steps[1:]] == [
        "Capability statement covering the information the notice asks for"
    ]
    assert packet.upload_steps[1].source_req_ids == ["R-004"]
    # a portal-side item (SAM registration) is never an upload step
    assert not any("SAM.gov registration" in s.label for s in packet.upload_steps)

    assert packet.sam_login_note == SAM_LOGIN_NOTE and packet.dsc_steps == []
    assert [s.key for s in packet.signatures] == ["offer_form", "reps_certs"]
    assert "SAM.gov" in packet.signatures[1].signed_by
    assert packet.page_limit == 10 and packet.copies is None
    assert packet.disclaimer == NEVER_SUBMITS and "never submits" in packet.disclaimer


def test_deadline_is_rendered_in_the_buyer_and_the_user_zone() -> None:
    packet = _us_packet()
    assert packet.deadline is not None
    assert packet.deadline.utc == DUE
    assert packet.deadline.buyer_tz == "America/New_York" and "EDT" in packet.deadline.buyer_display
    assert packet.deadline.user_tz == "Asia/Kolkata" and "IST" in packet.deadline.user_display
    assert "=" in packet.deadline.display and "2026" in packet.deadline.buyer_display
    # no deadline on the notice yet: the field is simply absent
    assert _us_packet(due=None).deadline is None


def test_india_packet_has_two_covers_emd_bg_and_the_dsc_steps() -> None:
    rules = extract_format_rules(IN_REQS)
    checklist = build_checklist(
        ChecklistContext(region="in", source_id="cppp", currency="INR"), IN_REQS
    )
    packet = build_packet(
        PacketContext(
            region="in",
            notice_type="gem_bid",
            portal_url="https://eprocure.gov.in/tender/1",
            buyer_tz="Asia/Kolkata",
            response_due_at=DUE,
        ),
        rules,
        checklist,
    )
    labels = [s.label for s in packet.upload_steps]
    assert labels[:2] == ["Technical bid cover", "Financial bid cover / BoQ"]
    assert all("Class 3 DSC" in (s.note or "") for s in packet.upload_steps[:2])
    assert packet.upload_steps[0].destination == "Upload on CPPP (eprocure.gov.in)"
    # the required India attachments follow, and the EMD/tender-fee payments do not
    assert (
        "Chartered Accountant certified turnover statement for the last three financial years"
        in labels
    )
    assert any("affidavit" in label.lower() for label in labels)
    assert any("Power of attorney" in label for label in labels)
    assert any("bank guarantee" in label.lower() for label in labels)  # cited -> required
    assert not any("Earnest money" in label for label in labels)
    assert not any("Udyam" in label for label in labels)  # optional, not promoted

    assert packet.dsc_steps == list(DSC_STEPS) and len(packet.dsc_steps) == 5
    assert packet.sam_login_note is None
    assert [s.key for s in packet.signatures] == ["dsc_covers", "affidavit", "power_of_attorney"]
    assert "private key" in (packet.signatures[0].note or "")
    # only the buyer zone is known here
    assert packet.deadline is not None and packet.deadline.user_tz is None


def test_packet_falls_back_when_the_solicitation_says_nothing() -> None:
    bare = build_packet(PacketContext(region="us", notice_type="rfp"), FormatRules(), [])
    assert bare.portal is None and bare.submission_method is None
    assert [s.label for s in bare.upload_steps] == ["Proposal response"]
    assert bare.upload_steps[0].destination == "Upload on the buyer's portal"
    assert bare.upload_steps[0].formats == ["PDF"] and bare.upload_steps[0].file_name is None
    assert bare.upload_steps[0].note is None
    assert [s.key for s in bare.signatures] == ["offer_form"]  # no reps & certs item given
    assert bare.deadline is None
    # a portal with no format rule still gives a destination
    hinted = build_packet(
        PacketContext(region="us", portal_url="https://portal.test/x"), FormatRules(), []
    )
    assert hinted.portal == "https://portal.test/x"
    assert hinted.upload_steps[0].destination == "Upload on https://portal.test/x"
    with pytest.raises(ValueError):
        build_packet(PacketContext(region="eu"), FormatRules(), [])


def test_hard_copies_become_a_delivery_step_citing_the_requirement() -> None:
    rules = extract_format_rules(
        [Req("R-020", "Provide 3 hard copies at the pre-bid meeting.", "submission")]
    )
    assert rules.copies == 3
    packet = build_packet(PacketContext(region="us"), rules, [])
    last = packet.upload_steps[-1]
    assert (
        last.label == "3 hard copies" and last.destination == "Deliver as the solicitation directs"
    )
    assert last.formats == [] and last.source_req_ids == ["R-020"]
    assert packet.copies == 3
    assert build_packet(PacketContext(region="us"), FormatRules(), []).copies is None


def test_the_packet_module_performs_no_io_and_calls_nothing_that_submits() -> None:
    """SPEC 1: auto-submission is out of scope. The builder is pure -- it imports no HTTP
    client and defines no coroutine, so there is nothing it could call out to."""
    source = inspect.getsource(packet_module)
    for forbidden in ("httpx", "requests", "urllib", "async def", "await ", "subprocess", "open("):
        assert forbidden not in source, forbidden
    assert not any(inspect.iscoroutinefunction(value) for value in vars(packet_module).values())
