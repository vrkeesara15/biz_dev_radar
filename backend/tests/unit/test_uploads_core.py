"""M1-11: upload allow-list (extension + magic bytes), 50 MB cap, archive key helpers."""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.core.paths import raw_archive_key, safe_segment, tenant_file_key
from app.core.uploads import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    UnsupportedUploadType,
    UploadTooLarge,
    check_size,
    extension_of,
    sniff_kind,
    validate_upload,
)

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 16


def ooxml(prefix: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(f"{prefix}/document.xml", "<x/>")
    return buf.getvalue()


def test_allow_list_matches_spec() -> None:
    assert set(ALLOWED_EXTENSIONS) == {
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "png",
        "jpg",
        "jpeg",
        "txt",
        "csv",
    }
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024


@pytest.mark.parametrize(
    ("name", "data", "kind", "ctype"),
    [
        ("a.pdf", PDF, "pdf", "application/pdf"),
        ("A.PDF", PDF, "pdf", "application/pdf"),
        ("a.png", PNG, "png", "image/png"),
        ("a.jpg", JPG, "jpeg", "image/jpeg"),
        ("a.jpeg", JPG, "jpeg", "image/jpeg"),
        (
            "a.docx",
            ooxml("word"),
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "a.xlsx",
            ooxml("xl"),
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "a.pptx",
            ooxml("ppt"),
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ("notes.txt", "héllo\nworld".encode(), "text", "text/plain"),
        ("data.csv", b"a,b\n1,2\n", "text", "text/csv"),
        ("bom.txt", "﻿text".encode(), "text", "text/plain"),
        ("dir/sub/a.pdf", PDF, "pdf", "application/pdf"),
        ("win\\path\\a.pdf", PDF, "pdf", "application/pdf"),
    ],
)
def test_accepts_allowed_types(name: str, data: bytes, kind: str, ctype: str) -> None:
    check = validate_upload(name, data)
    assert check.kind == kind
    assert check.content_type == ctype
    assert check.size == len(data)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("virus.exe", b"MZ\x90\x00" + b"\x00" * 64),
        ("archive.zip", ooxml("word")),
        ("noext", PDF),
        (".pdf", PDF),
        ("script.js", b"alert(1)"),
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ("a.pdf.exe", PDF),
    ],
)
def test_rejects_extensions_outside_allow_list(name: str, data: bytes) -> None:
    with pytest.raises(UnsupportedUploadType) as exc:
        validate_upload(name, data)
    assert exc.value.status == 415
    assert exc.value.details["allowed"] == sorted(ALLOWED_EXTENSIONS)


@pytest.mark.parametrize(
    ("name", "data", "detected"),
    [
        ("renamed.pdf", PNG, "png"),
        ("renamed.png", PDF, "pdf"),
        ("renamed.docx", ooxml("xl"), "xlsx"),
        ("plainzip.docx", ooxml("other"), "zip"),
        ("renamed.jpg", b"MZ" + b"\x00" * 40, "exe"),
        ("garbage.pdf", b"not a pdf at all", "unknown"),
        ("binary.txt", b"\x00\x01\x02binary", "binary"),
        ("pdf.csv", PDF, "pdf"),
        ("latin1.txt", "caf\xe9".encode("latin-1"), "binary"),
        ("exe.txt", b"MZ" + b"\x00" * 40, "exe"),
    ],
)
def test_rejects_content_that_disagrees_with_extension(
    name: str, data: bytes, detected: str
) -> None:
    with pytest.raises(UnsupportedUploadType) as exc:
        validate_upload(name, data)
    assert exc.value.status == 415
    assert exc.value.details["detected"] == detected


def test_size_cap_is_413() -> None:
    check_size(MAX_UPLOAD_BYTES)
    with pytest.raises(UploadTooLarge) as exc:
        check_size(MAX_UPLOAD_BYTES + 1)
    assert exc.value.status == 413
    assert exc.value.details == {"size": MAX_UPLOAD_BYTES + 1, "limit": MAX_UPLOAD_BYTES}
    with pytest.raises(UploadTooLarge):
        validate_upload("a.txt", b"x" * 11, limit=10)
    assert validate_upload("a.txt", b"x" * 10, limit=10).size == 10
    with pytest.raises(ValueError):
        check_size(-1)


def test_size_is_checked_before_type() -> None:
    with pytest.raises(UploadTooLarge):
        validate_upload("big.exe", b"MZ" * 8, limit=4)


def test_sniff_kind_and_extension_helpers() -> None:
    assert sniff_kind(PDF) == "pdf"
    assert sniff_kind(b"PK\x03\x04broken") == "zip"
    assert sniff_kind(b"") is None
    assert sniff_kind(b"hello") is None
    assert extension_of("x.tar.gz") == "gz"
    assert extension_of("README") == ""
    assert extension_of("/tmp/.hidden") == ""
    assert extension_of(".hidden.txt") == "txt"


# --- object keys -----------------------------------------------------------------------


def test_raw_archive_key_layout() -> None:
    at = datetime(2026, 9, 26, 10, 5, 7, tzinfo=UTC)
    assert raw_archive_key("sam_gov", "abc-123", at) == (
        "raw/sam_gov/2026/09/26/abc-123/2026-09-26T10:05:07Z"
    )


def test_raw_archive_key_normalizes_to_utc_and_escapes() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    at = datetime(2026, 1, 1, 2, 0, 0, tzinfo=ist)  # 2025-12-31T20:30:00Z
    key = raw_archive_key("gem", "GEM/2026/B/1 2", at)
    assert key == "raw/gem/2025/12/31/GEM%2F2026%2FB%2F1%202/2025-12-31T20:30:00Z"
    with pytest.raises(ValueError):
        raw_archive_key("gem", "x", datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        raw_archive_key("gem", "..", at)
    with pytest.raises(ValueError):
        safe_segment("   ")


def test_tenant_file_key() -> None:
    tid, fid = uuid.uuid4(), uuid.uuid4()
    assert tenant_file_key(tid, fid, "PDF") == f"tenants/{tid}/files/{fid}.pdf"
    assert tenant_file_key(tid, fid, ".txt") == f"tenants/{tid}/files/{fid}.txt"
    with pytest.raises(ValueError):
        tenant_file_key(tid, fid, "../x")
