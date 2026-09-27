"""Upload validation (pure): allow-list by extension AND sniffed content, 50 MB cap (SPEC 11).

    check = validate_upload("brochure.pdf", data)   # UploadCheck or raises
    -> UploadTooLarge (HTTP 413) / UnsupportedUploadType (HTTP 415)

The sniffed kind must agree with the extension: a PNG renamed to .pdf is refused.
Office formats are zip containers, so the zip directory is inspected for the
word/, xl/ or ppt/ part prefix. txt/csv have no magic: they must be UTF-8 text
without NUL bytes and must not carry another format's signature.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# extension -> canonical kind
ALLOWED_EXTENSIONS: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "txt": "text",
    "csv": "text",
}

CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "txt": "text/plain",
    "csv": "text/csv",
}

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),  # empty zip
    (b"MZ", "exe"),
    (b"\x7fELF", "elf"),
    (b"GIF8", "gif"),
    (b"Rar!", "rar"),
    (b"\x1f\x8b", "gzip"),
    (b"\xd0\xcf\x11\xe0", "ole"),  # legacy .doc/.xls/.ppt
)

_OOXML_PREFIXES: tuple[tuple[str, str], ...] = (
    ("word/", "docx"),
    ("xl/", "xlsx"),
    ("ppt/", "pptx"),
)


class UploadRejected(ValueError):  # noqa: N818 - domain name, mapped to HTTP status
    """Base class; `status` is the HTTP status the API answers with."""

    status = 400

    def __init__(self, reason: str, **details: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


class UploadTooLarge(UploadRejected):
    status = 413


class UnsupportedUploadType(UploadRejected):
    status = 415


@dataclass(frozen=True, slots=True)
class UploadCheck:
    extension: str  # normalized, without the dot
    kind: str  # canonical sniffed kind
    content_type: str
    size: int


def extension_of(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    if "." not in name or (name.startswith(".") and name.count(".") == 1):
        return ""
    return name.rsplit(".", 1)[1].lower()


def check_size(size: int, limit: int = MAX_UPLOAD_BYTES) -> None:
    if size < 0:
        raise ValueError("size must be >= 0")
    if size > limit:
        raise UploadTooLarge("file exceeds the upload size limit", size=size, limit=limit)


def sniff_kind(data: bytes) -> str | None:
    """Canonical kind from magic bytes; zip containers are refined to docx/xlsx/pptx.
    None means no known binary signature (candidate for text)."""
    head = data[:16]
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return _ooxml_kind(data) if kind == "zip" else kind
    return None


def _ooxml_kind(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return "zip"
    if "[Content_Types].xml" not in names:
        return "zip"
    for prefix, kind in _OOXML_PREFIXES:
        if any(n.startswith(prefix) for n in names):
            return kind
    return "zip"


def looks_like_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def validate_upload(filename: str, data: bytes, *, limit: int = MAX_UPLOAD_BYTES) -> UploadCheck:
    """Full check on the complete payload. Order: size (413), extension (415), content (415)."""
    check_size(len(data), limit)
    ext = extension_of(filename)
    expected = ALLOWED_EXTENSIONS.get(ext)
    if expected is None:
        raise UnsupportedUploadType(
            "file extension is not allowed",
            extension=ext or None,
            allowed=sorted(ALLOWED_EXTENSIONS),
        )
    sniffed = sniff_kind(data)
    if expected == "text":
        if sniffed is not None or not looks_like_text(data):
            raise UnsupportedUploadType(
                "file content is not plain text", extension=ext, detected=sniffed or "binary"
            )
        kind = "text"
    elif sniffed != expected:
        raise UnsupportedUploadType(
            "file content does not match its extension",
            extension=ext,
            detected=sniffed or "unknown",
            expected=expected,
        )
    else:
        kind = expected
    return UploadCheck(extension=ext, kind=kind, content_type=CONTENT_TYPES[ext], size=len(data))
