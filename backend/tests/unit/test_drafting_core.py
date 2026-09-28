"""M5-08: Markdown rendering / HTML sanitising, the fan-out helper and the citation
resolution that turns an unbacked claim into a [NEEDS INPUT] marker."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.agents.drafters import NEEDS_INPUT_EVIDENCE, Claim, resolve_body
from app.core.citations import kb_token, profile_token
from app.core.markdown import markdown_to_html, sanitize_html
from app.services.agents import fan_out

PP = uuid.uuid4()
PP_TOKEN = profile_token("past_performance", PP)
KB = kb_token("boilerplate", uuid.uuid4(), 0)
ALLOWED = {PP_TOKEN: "Treasury migration", KB: "company overview"}


# --- markdown and sanitising ------------------------------------------------------------


def test_markdown_becomes_html_and_keeps_citation_tokens() -> None:
    html = markdown_to_html(
        f"## Approach\n\nWe migrated 400 workloads [{PP_TOKEN}].\n\n- one\n- two\n"
    )
    assert "<h2>Approach</h2>" in html
    assert f"[{PP_TOKEN}]" in html  # the token survives rendering
    assert "<li>one</li>" in html


def test_raw_html_in_markdown_is_escaped_not_rendered() -> None:
    html = markdown_to_html("Hello <script>alert(1)</script> <b>bold</b>")
    assert "<script>" not in html and "&lt;script&gt;" in html


@pytest.mark.parametrize(
    ("dirty", "gone"),
    [
        ('<p onclick="steal()">x</p>', "onclick"),
        ('<a href="javascript:alert(1)">x</a>', "javascript"),
        ('<iframe src="https://evil.test"></iframe>', "iframe"),
        ("<style>body{display:none}</style>", "style"),
        ('<img src="https://evil.test/p.gif">', "img"),
        ("<!-- secret -->", "secret"),
    ],
)
def test_sanitizer_drops_dangerous_html(dirty: str, gone: str) -> None:
    assert gone not in sanitize_html(dirty)


def test_sanitizer_keeps_proposal_markup() -> None:
    html = sanitize_html(
        "<h3>Staffing</h3><p><strong>Ada</strong> leads</p>"
        '<table><tr><th scope="col">Role</th><td colspan="2">PM</td></tr></table>'
        '<a href="https://sam.gov" title="portal">SAM</a>'
    )
    assert "<h3>Staffing</h3>" in html and "<strong>Ada</strong>" in html
    assert 'colspan="2"' in html and 'scope="col"' in html
    assert 'href="https://sam.gov"' in html and 'rel="noopener noreferrer"' in html


# --- fan-out ------------------------------------------------------------------------------


async def test_fan_out_keeps_order_and_bounds_concurrency() -> None:
    running = 0
    peak = 0

    async def worker(item: int) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1
        return item * 2

    assert await fan_out(list(range(6)), worker, concurrency=2) == [0, 2, 4, 6, 8, 10]
    assert peak <= 2
    assert await fan_out([], worker) == []


async def test_fan_out_propagates_the_first_failure() -> None:
    async def worker(item: int) -> int:
        if item == 2:
            raise ValueError("volume 2 failed")
        return item

    with pytest.raises(ValueError, match="volume 2 failed"):
        await fan_out([1, 2, 3], worker, concurrency=3)


# --- citation resolution --------------------------------------------------------------------


def test_resolved_citations_are_left_alone() -> None:
    body = f"We migrated 400 workloads [{PP_TOKEN}] on time."
    out, unresolved, uncited = resolve_body(
        body, ALLOWED, [Claim(sentence="We migrated 400 workloads", citation_token=PP_TOKEN)]
    )
    assert out == body and unresolved == [] and uncited == []


def test_an_unresolvable_token_is_replaced_with_needs_input() -> None:
    ghost = profile_token("past_performance", uuid.uuid4())
    body = f"We hold a CMMI Level 5 appraisal [{ghost}]."
    out, unresolved, _ = resolve_body(body, ALLOWED, [])
    assert unresolved == [ghost]
    assert ghost not in out and NEEDS_INPUT_EVIDENCE in out


def test_an_uncited_claim_is_marked_in_the_body() -> None:
    body = "We employ 250 engineers. We run a 24x7 operations centre."
    out, unresolved, uncited = resolve_body(
        body,
        ALLOWED,
        [
            Claim(sentence="We employ 250 engineers.", citation_token=None),
            Claim(sentence="We run a 24x7 operations centre.", citation_token=PP_TOKEN),
        ],
    )
    assert unresolved == [] and uncited == ["We employ 250 engineers."]
    assert f"We employ 250 engineers. {NEEDS_INPUT_EVIDENCE}" in out
    assert out.count(NEEDS_INPUT_EVIDENCE) == 1


def test_a_claim_citing_another_tenants_record_is_not_trusted() -> None:
    foreign = kb_token("past_performance", uuid.uuid4(), 1)
    out, unresolved, uncited = resolve_body(
        "Our revenue grew 40%.",
        ALLOWED,
        [Claim(sentence="Our revenue grew 40%.", citation_token=foreign)],
    )
    assert uncited == ["Our revenue grew 40%."] and unresolved == []
    assert NEEDS_INPUT_EVIDENCE in out
