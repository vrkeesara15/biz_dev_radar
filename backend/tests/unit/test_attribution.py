"""M3-10 (SPEC 11): source attribution, the per-record line and the disclaimers."""

from __future__ import annotations

import pytest
from app.adapters.gepnic import load_portal_configs
from app.adapters.registry import load_builtin_adapters, registered
from app.core.attribution import PORTAL_HOME, SOURCE_NAMES, portal_url, source_name
from app.core.disclaimers import (
    AI_DRAFT,
    VERIFY_ON_PORTAL,
    attribution_text,
    record_footer,
)

TN_URL = "https://tntenders.gov.in/nicgep/app?component=%24DirectLink&sp=Sx"


def test_attribution_text_names_the_portal_and_the_official_link() -> None:
    assert attribution_text("gepnic_tn", TN_URL) == (
        f"Source: Tamil Nadu Tenders (tntenders.gov.in) · Official notice: {TN_URL}"
    )
    assert attribution_text("cppp", None) == (
        "Source: Central Public Procurement Portal (eprocure.gov.in) · "
        "Official notice: https://eprocure.gov.in/"
    )
    # a source with neither a record link nor a portal home still names the source
    assert attribution_text("mirror_portal", None) == "Source: mirror_portal"


def test_record_footer_adds_the_verify_on_portal_disclaimer() -> None:
    footer = record_footer("gem", None)
    assert footer.startswith(attribution_text("gem", None))
    assert footer.endswith(VERIFY_ON_PORTAL)
    assert AI_DRAFT not in footer  # the AI label belongs on drafts, not on the record


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("gepnic_tn", "Tamil Nadu Tenders (tntenders.gov.in)"),
        ("gepnic_up", "Uttar Pradesh e-Tender (etender.up.nic.in)"),
        ("gepnic_central", "Central GePNIC e-Tenders (etenders.gov.in)"),
        ("gepnic_mh", "Maharashtra Tenders (mahatenders.gov.in)"),
        ("gepnic_ts", "Telangana eProcurement (eprocurement.telangana.gov.in)"),
        ("cppp", "Central Public Procurement Portal (eprocure.gov.in)"),
        ("gem", "Government e-Marketplace (GeM)"),
    ],
)
def test_india_sources_have_a_display_name(source_id: str, expected: str) -> None:
    assert source_name(source_id) == expected


def test_an_unlisted_gepnic_state_still_reads_as_a_portal_not_an_id() -> None:
    assert source_name("gepnic_kl") == "GePNIC state portal (KL)"
    assert source_name("something_else") == "something_else"


def test_portal_url_prefers_the_records_own_page() -> None:
    assert portal_url("gepnic_tn", TN_URL) == TN_URL
    assert portal_url("gepnic_tn", None) == "https://tntenders.gov.in/"
    assert portal_url("mirror_portal", None) is None


def test_the_gepnic_names_match_the_portal_config() -> None:
    """core/attribution.py duplicates the YAML (core stays I/O-free): keep them equal."""
    for config in load_portal_configs():
        assert SOURCE_NAMES[config.source_id] == config.display_name, config.source_id
        assert PORTAL_HOME[config.source_id] == config.portal_home, config.source_id


def test_every_registered_source_has_a_display_name() -> None:
    load_builtin_adapters()
    missing = [
        source_id
        for source_id in registered()
        if source_name(source_id) == source_id and not source_id.startswith("gepnic_")
    ]
    assert not missing, f"sources without a display name in core/attribution.py: {missing}"
