"""Source attribution (SPEC 11: show the source and a link back to the official portal on
every record). Display names are data; unknown ids fall back to the id itself.

The `gepnic_*` entries must match `display_name` / `portal_home` in
`app/adapters/gepnic_configs.yaml`; `tests/unit/test_attribution.py` fails when they
drift. They are duplicated rather than read from the YAML because `core/` stays free of
I/O (and of an import of `app.adapters`, which imports `core`).
"""

from __future__ import annotations

SOURCE_NAMES: dict[str, str] = {
    "sam_opps": "SAM.gov Contract Opportunities",
    "sam_awards": "SAM.gov Contract Awards",
    "usaspending": "USAspending.gov",
    "grants_gov": "Grants.gov",
    "defense_gov_awards": "Defense.gov Contract Announcements",
    "sled_generic": "US state and local portals",
    "cppp": "Central Public Procurement Portal (eprocure.gov.in)",
    "gem": "Government e-Marketplace (GeM)",
    "gepnic_tn": "Tamil Nadu Tenders (tntenders.gov.in)",
    "gepnic_up": "Uttar Pradesh e-Tender (etender.up.nic.in)",
    "gepnic_central": "Central GePNIC e-Tenders (etenders.gov.in)",
    "gepnic_mh": "Maharashtra Tenders (mahatenders.gov.in)",
    "gepnic_ts": "Telangana eProcurement (eprocurement.telangana.gov.in)",
    "ireps": "IREPS (Indian Railways)",
    "defproc": "Defence Procurement Portal (defproc.gov.in)",
    "highergov": "HigherGov",
    "govspend": "GovSpend",
    "bidnet": "BidNet Direct",
    "tendertiger": "TenderTiger",
    "tender247": "Tender247",
    "bidassist": "BidAssist",
}
PORTAL_HOME: dict[str, str] = {
    "sam_opps": "https://sam.gov/",
    "sam_awards": "https://sam.gov/",
    "usaspending": "https://www.usaspending.gov/",
    "grants_gov": "https://www.grants.gov/",
    "cppp": "https://eprocure.gov.in/",
    "gem": "https://gem.gov.in/",
    "gepnic_tn": "https://tntenders.gov.in/",
    "gepnic_up": "https://etender.up.nic.in/",
    "gepnic_central": "https://etenders.gov.in/",
    "gepnic_mh": "https://mahatenders.gov.in/",
    "gepnic_ts": "https://eprocurement.telangana.gov.in/",
    "ireps": "https://www.ireps.gov.in/",
    "defproc": "https://defproc.gov.in/",
}


def source_name(source_id: str) -> str:
    known = SOURCE_NAMES.get(source_id)
    if known:
        return known
    if source_id.startswith("gepnic_"):  # a state added to the YAML but not to the table
        return f"GePNIC state portal ({source_id.removeprefix('gepnic_').upper()})"
    return source_id


def portal_url(source_id: str, source_url: str | None) -> str | None:
    """The record's own page when the source published one, else the portal home."""
    return source_url or PORTAL_HOME.get(source_id)
