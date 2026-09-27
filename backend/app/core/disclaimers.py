"""Product disclaimers and the per-record attribution line (SPEC 11).

Every rendering of a notice - the API response, an export, a digest email - carries the
same two things: who published it with a link back to the official page, and the
reminder that the portal is authoritative.

    attribution_text("gepnic_tn", row.source_url)
        "Source: Tamil Nadu Tenders (tntenders.gov.in) · Official notice: https://..."
    record_footer("gepnic_tn", row.source_url)
        "... · Verify every detail on the official portal before submitting."

Pure: exports (M7) and notification emails (M4) must render `record_footer` rather than
compose their own wording.
"""

from __future__ import annotations

from app.core.attribution import portal_url, source_name

VERIFY_ON_PORTAL = "Verify every detail on the official portal before submitting."
AI_DRAFT = "AI-generated draft. Review before use."

SEPARATOR = " \u00b7 "
SOURCE_PREFIX = "Source: "
NOTICE_PREFIX = "Official notice: "


def attribution_text(source_id: str, source_url: str | None = None) -> str:
    """ "Source: <portal name> · Official notice: <url>" (the link is dropped only when
    neither the record nor the portal table has one)."""
    parts = [f"{SOURCE_PREFIX}{source_name(source_id)}"]
    link = portal_url(source_id, source_url)
    if link:
        parts.append(f"{NOTICE_PREFIX}{link}")
    return SEPARATOR.join(parts)


def record_footer(source_id: str, source_url: str | None = None) -> str:
    """The attribution line plus the verify-on-portal disclaimer, as one line."""
    return f"{attribution_text(source_id, source_url)}{SEPARATOR}{VERIFY_ON_PORTAL}"
