"""Source attribution (SPEC 11: show the source and a link back to the official portal on
every record). Display names are data; unknown ids fall back to the id itself."""

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
    "ireps": "IREPS (Indian Railways)",
    "defproc": "Defence Procurement Portal (defproc.gov.in)",
}
PORTAL_HOME: dict[str, str] = {
    "sam_opps": "https://sam.gov/",
    "sam_awards": "https://sam.gov/",
    "usaspending": "https://www.usaspending.gov/",
    "grants_gov": "https://www.grants.gov/",
    "cppp": "https://eprocure.gov.in/",
    "gem": "https://gem.gov.in/",
    "ireps": "https://www.ireps.gov.in/",
    "defproc": "https://defproc.gov.in/",
}


def source_name(source_id: str) -> str:
    if source_id.startswith("gepnic_"):
        return f"GePNIC state portal ({source_id.removeprefix('gepnic_').upper()})"
    return SOURCE_NAMES.get(source_id, source_id)


def portal_url(source_id: str, source_url: str | None) -> str | None:
    """The record's own page when the source published one, else the portal home."""
    return source_url or PORTAL_HOME.get(source_id)
