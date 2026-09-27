"""Object-store key builders (pure). Keys are region-agnostic: the bucket carries residency.

raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at}   raw source payloads (SPEC 5.3 raw_ref)
tenants/{tenant_id}/files/{file_id}.{ext}                  tenant uploads (SPEC 4.5, 11)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import quote

RAW_PREFIX = "raw"
TENANT_PREFIX = "tenants"
_SAFE = "-_.~"


def safe_segment(value: str) -> str:
    """Percent-encode anything that could change the key's path structure."""
    text = value.strip()
    if not text:
        raise ValueError("key segment must not be empty")
    if text in {".", ".."}:
        raise ValueError("key segment must not be a relative path component")
    return quote(text, safe=_SAFE)


def utc_stamp(moment: datetime) -> str:
    """ISO-8601 UTC timestamp with second precision and a Z suffix (safe in object keys)."""
    if moment.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw_archive_key(source: str, external_id: str, fetched_at: datetime) -> str:
    """raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at_iso} (SPEC 5.3 raw_ref)."""
    at = fetched_at.astimezone(UTC) if fetched_at.tzinfo else None
    if at is None:
        raise ValueError("fetched_at must be timezone-aware")
    return "/".join(
        [
            RAW_PREFIX,
            safe_segment(source),
            f"{at.year:04d}",
            f"{at.month:02d}",
            f"{at.day:02d}",
            safe_segment(external_id),
            utc_stamp(at),
        ]
    )


def tenant_file_key(tenant_id: uuid.UUID, file_id: uuid.UUID, extension: str) -> str:
    """tenants/{tenant_id}/files/{file_id}.{ext}; the extension is the validated one."""
    ext = extension.lower().lstrip(".")
    if not ext.isalnum():
        raise ValueError(f"invalid extension {extension!r}")
    return f"{TENANT_PREFIX}/{tenant_id}/files/{file_id}.{ext}"
