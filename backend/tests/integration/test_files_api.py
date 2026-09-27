"""M1-11: POST /api/v1/files (allow-list, 50 MB cap, scan, residency bucket) and signed URLs."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.roles import Role
from app.core.uploads import MAX_UPLOAD_BYTES
from app.models import AuditLog, File
from app.services.scanner import ScannerUnavailableError, ScanResult
from app.services.storage import LocalStorage
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class FakeScanner:
    name = "fake"

    def __init__(self, result: ScanResult | Exception) -> None:
        self.result = result
        self.calls = 0

    async def scan(self, data: bytes) -> ScanResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def _tenant(database: Database, **overrides):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id, user.id


def _part(name: str, data: bytes, ctype: str = "application/octet-stream") -> dict[str, object]:
    return {"file": (name, data, ctype)}


def _stored(root: str, tenant_id: uuid.UUID) -> list[Path]:
    """Object files of one tenant under the (session-shared) local storage root."""
    return [p for p in Path(root).rglob("*") if p.is_file() and str(tenant_id) in str(p)]


async def _audit(database: Database, action: str) -> list[AuditLog]:
    async with database.owner_session() as session:
        rows = await session.execute(select(AuditLog).where(AuditLog.action == action))
        return list(rows.scalars().all())


async def test_upload_stores_object_in_residency_bucket_and_records_row(
    api_client: httpx.AsyncClient,
    database: Database,
    settings,
    app,  # type: ignore[no-untyped-def]
) -> None:
    tid, uid = await _tenant(database, region=Region.IN, data_residency=Region.IN)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    r = await api_client.post(
        "/api/v1/files", files=_part("Capability Statement.pdf", PDF), headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "Capability Statement.pdf"
    assert body["extension"] == "pdf" and body["kind"] == "pdf"
    assert body["content_type"] == "application/pdf"
    assert body["size_bytes"] == len(PDF)
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["region"] == "in" and body["scan_status"] == "clean"
    file_id = uuid.UUID(body["id"])

    async with database.owner_session() as session:
        row = await session.get(File, file_id)
    assert row is not None
    assert row.tenant_id == tid and row.uploaded_by == uid
    assert row.bucket == settings.s3_bucket_in == "bidradar-in"
    assert row.key == f"tenants/{tid}/files/{file_id}.pdf"
    assert row.scanner == "noop"
    stored = Path(settings.local_storage_root) / "bidradar-in" / row.key
    assert stored.read_bytes() == PDF
    storage = app.state.storage_router.for_region("in")
    assert isinstance(storage, LocalStorage)
    assert await storage.get(row.key) == PDF

    uploads = await _audit(database, "file.upload")
    assert len(uploads) == 1
    assert uploads[0].object_id == str(file_id) and uploads[0].meta["kind"] == "pdf"


async def test_us_tenant_uses_us_bucket(
    api_client: httpx.AsyncClient, database: Database, settings
) -> None:  # type: ignore[no-untyped-def]
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.BID_MANAGER)
    r = await api_client.post("/api/v1/files", files=_part("notes.txt", b"hello"), headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["region"] == "us" and r.json()["content_type"] == "text/plain"
    async with database.owner_session() as session:
        row = await session.get(File, uuid.UUID(r.json()["id"]))
    assert row is not None and row.bucket == "bidradar-us"
    assert (Path(settings.local_storage_root) / "bidradar-us" / row.key).is_file()


@pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
async def test_read_only_roles_cannot_upload(
    api_client: httpx.AsyncClient, database: Database, role: Role
) -> None:
    tid, uid = await _tenant(database)
    r = await api_client.post(
        "/api/v1/files",
        files=_part("notes.txt", b"hello"),
        headers=auth_headers(user_id=uid, tenant_id=tid, role=role),
    )
    assert r.status_code == 403
    async with database.owner_session() as session:
        assert (await session.execute(select(File))).scalars().all() == []


@pytest.mark.parametrize(
    ("name", "data", "detected"),
    [
        ("tool.exe", b"MZ\x90\x00" + b"\x00" * 32, None),
        ("bundle.zip", b"PK\x03\x04" + b"\x00" * 32, None),
        ("renamed.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "png"),
        ("binary.txt", b"\x00\x01\x02", "binary"),
        ("noext", PDF, None),
    ],
)
async def test_disallowed_types_are_415_and_audited(
    api_client: httpx.AsyncClient,
    database: Database,
    settings,  # type: ignore[no-untyped-def]
    name: str,
    data: bytes,
    detected: str | None,
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    r = await api_client.post("/api/v1/files", files=_part(name, data), headers=headers)
    assert r.status_code == 415, r.text
    detail = r.json()["detail"]
    if detected is None:
        assert detail["error"] == "file extension is not allowed"
        assert "pdf" in detail["allowed"]
    else:
        assert detail["detected"] == detected
    rejected = await _audit(database, "file.rejected")
    assert len(rejected) == 1
    assert rejected[0].meta["filename"] == name and rejected[0].meta["status"] == 415
    async with database.owner_session() as session:
        assert (await session.execute(select(File))).scalars().all() == []
    assert _stored(settings.local_storage_root, tid) == []


async def test_upload_over_50mb_is_413(api_client: httpx.AsyncClient, database: Database) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    big = b"a" * (MAX_UPLOAD_BYTES + 1)
    r = await api_client.post("/api/v1/files", files=_part("big.txt", big), headers=headers)
    assert r.status_code == 413, r.text[:200]
    assert r.json()["detail"]["limit"] == MAX_UPLOAD_BYTES
    rejected = await _audit(database, "file.rejected")
    assert len(rejected) == 1 and rejected[0].meta["status"] == 413
    # exactly the cap is accepted
    r = await api_client.post(
        "/api/v1/files", files=_part("max.txt", b"a" * MAX_UPLOAD_BYTES), headers=headers
    )
    assert r.status_code == 201, r.text[:200]
    assert r.json()["size_bytes"] == MAX_UPLOAD_BYTES


async def test_declared_oversize_body_is_rejected_before_reading(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    headers["Content-Length"] = str(MAX_UPLOAD_BYTES * 3)
    r = await api_client.post("/api/v1/files", files=_part("x.txt", b"tiny"), headers=headers)
    assert r.status_code == 413


async def test_infected_file_is_rejected_and_audited(
    api_client: httpx.AsyncClient,
    database: Database,
    app,
    settings,  # type: ignore[no-untyped-def]
) -> None:
    scanner = FakeScanner(
        ScanResult(clean=False, signature="Win.Test.EICAR_HDB-1", scanner="clamav")
    )
    app.state.scanner = scanner
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.TENANT_OWNER)
    r = await api_client.post(
        "/api/v1/files", files=_part("eicar.txt", b"X5O!P%@AP"), headers=headers
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "error": "file failed the virus scan",
        "signature": "Win.Test.EICAR_HDB-1",
        "scanner": "clamav",
    }
    assert scanner.calls == 1
    rejected = await _audit(database, "file.rejected")
    assert len(rejected) == 1
    row = rejected[0]
    assert row.tenant_id == tid and row.user_id == uid
    assert row.meta["reason"] == "file failed the virus scan"
    assert row.meta["signature"] == "Win.Test.EICAR_HDB-1"
    assert row.meta["filename"] == "eicar.txt" and row.meta["status"] == 422
    async with database.owner_session() as session:
        assert (await session.execute(select(File))).scalars().all() == []
    assert _stored(settings.local_storage_root, tid) == []


async def test_scanner_outage_fails_closed(
    api_client: httpx.AsyncClient,
    database: Database,
    app,  # type: ignore[no-untyped-def]
) -> None:
    app.state.scanner = FakeScanner(ScannerUnavailableError("clamd down"))
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    r = await api_client.post("/api/v1/files", files=_part("ok.txt", b"fine"), headers=headers)
    assert r.status_code == 503
    rejected = await _audit(database, "file.rejected")
    assert len(rejected) == 1 and rejected[0].meta["reason"] == "scanner_unavailable"
    async with database.owner_session() as session:
        assert (await session.execute(select(File))).scalars().all() == []


async def test_signed_url_expires_in_15_minutes_and_is_tenant_scoped(
    api_client: httpx.AsyncClient,
    database: Database,
    app,
    settings,  # type: ignore[no-untyped-def]
) -> None:
    tid, uid = await _tenant(database)
    other_tid, other_uid = await _tenant(database)
    writer = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    created = await api_client.post("/api/v1/files", files=_part("a.csv", b"a,b\n"), headers=writer)
    assert created.status_code == 201
    file_id = created.json()["id"]

    viewer = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.VIEWER)
    before = datetime.now(UTC)
    r = await api_client.get(f"/api/v1/files/{file_id}/url", headers=viewer)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["id"] == file_id and body["expires_in"] == 900 == settings.signed_url_expires_seconds
    )
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert 890 <= (expires_at - before).total_seconds() <= 910
    storage = app.state.storage_router.for_region("us")
    assert storage.verify(body["url"]) == f"tenants/{tid}/files/{file_id}.csv"

    meta = await api_client.get(f"/api/v1/files/{file_id}", headers=viewer)
    assert meta.status_code == 200 and meta.json()["filename"] == "a.csv"

    # another tenant sees neither the metadata nor a URL
    stranger = auth_headers(user_id=other_uid, tenant_id=other_tid, role=Role.TENANT_OWNER)
    assert (await api_client.get(f"/api/v1/files/{file_id}", headers=stranger)).status_code == 404
    assert (
        await api_client.get(f"/api/v1/files/{file_id}/url", headers=stranger)
    ).status_code == 404
    assert (
        await api_client.get(f"/api/v1/files/{uuid.uuid4()}/url", headers=writer)
    ).status_code == 404
    # signed-url reads are not mutations: nothing extra in the audit log
    assert len(await _audit(database, "file.upload")) == 1
