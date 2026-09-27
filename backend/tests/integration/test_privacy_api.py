"""M7-07: consent, data-principal requests, tenant export and tenant erasure.

Celery runs eager in tests (settings fixture), so POST /tenant/export and /tenant/delete
do their work before the response; the assertions below are on the real rows, the real
zip in local object storage and the real deletes.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.core.config import Region, Settings
from app.core.db import Database
from app.core.privacy import SUB_PROCESSORS
from app.core.roles import Role
from app.main import create_app
from app.models import AuditLog, CompanyProfile, Consent, DataRequest, File, Tenant, UsageLedger
from app.services.privacy import erasable_tables, tenant_tables
from app.services.storage import StorageRouter
from fastapi import FastAPI
from sqlalchemy import func, select, text

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

GRIEVANCE = {
    "grievance_officer_name": "Asha Rao",
    "grievance_officer_email": "grievance@bidradar.example",
}


@pytest.fixture()
def privacy_settings(settings: Settings) -> Settings:
    return settings.model_copy(update=GRIEVANCE)


@pytest.fixture()
def privacy_app(privacy_settings: Settings, fake_embeddings: Any) -> FastAPI:
    application = create_app(privacy_settings)
    application.state.embeddings = fake_embeddings
    return application


@pytest.fixture()
async def privacy_client(privacy_app: FastAPI, clean_db: Any) -> Any:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=privacy_app, client=("203.0.113.10", 51000)),
        base_url="http://test",
    ) as client:
        yield client


class Seed:
    def __init__(self, tenant_id: uuid.UUID, user_id: uuid.UUID, email: str) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.email = email

    def headers(self, role: Role = Role.TENANT_OWNER) -> dict[str, str]:
        return auth_headers(
            user_id=self.user_id, tenant_id=self.tenant_id, role=role, email=self.email
        )


async def seed_tenant(database: Database, **overrides: Any) -> Seed:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return Seed(tenant.id, user.id, user.email)


async def count(database: Database, model: Any, tenant_id: uuid.UUID) -> int:
    async with database.owner_session() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
                )
            ).scalar_one()
        )


# --- public notice --------------------------------------------------------------------------


async def test_privacy_page_is_public(privacy_client: httpx.AsyncClient) -> None:
    r = await privacy_client.get("/api/v1/privacy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dpdp_notice_version"] == "v1"
    assert body["privacy_policy_version"] == "v1"
    assert body["terms_version"] == "v1"
    assert body["data_request_sla_days"] == 30
    assert body["grievance_officer"] == {
        "name": "Asha Rao",
        "email": "grievance@bidradar.example",
    }
    names = [p["name"] for p in body["sub_processors"]]
    assert names == [p.name for p in SUB_PROCESSORS]
    anthropic = next(p for p in body["sub_processors"] if p["name"] == "Anthropic")
    assert "zero-data-retention" in anthropic["notes"]


async def test_privacy_page_without_a_grievance_officer_configured(
    api_client: httpx.AsyncClient,
) -> None:
    r = await api_client.get("/api/v1/privacy")
    assert r.status_code == 200
    assert r.json()["grievance_officer"] == {"name": None, "email": None}


# --- consent ---------------------------------------------------------------------------------


async def test_consent_is_recorded_with_version_timestamp_and_ip(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database, region=Region.IN, data_residency=Region.IN)
    before = datetime.now(UTC)
    r = await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "dpdp", "version": "v1"}, headers=seed.headers()
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "dpdp" and body["version"] == "v1" and body["created"] is True
    accepted = datetime.fromisoformat(body["accepted_at"])
    assert before <= accepted <= datetime.now(UTC)

    async with database.owner_session() as session:
        row = (
            await session.execute(select(Consent).where(Consent.tenant_id == seed.tenant_id))
        ).scalar_one()
    assert row.user_id == seed.user_id and row.ip == "203.0.113.10"

    audit = [
        a
        for a in (await _rows_of(database, AuditLog, seed.tenant_id))
        if a.action == "privacy.consent"
    ]
    assert len(audit) == 1
    assert audit[0].meta["kind"] == "dpdp" and audit[0].meta["created"] is True


async def _rows_of(database: Database, model: Any, tenant_id: uuid.UUID) -> list[Any]:
    async with database.owner_session() as session:
        return list(
            (await session.execute(select(model).where(model.tenant_id == tenant_id))).scalars()
        )


async def test_re_accepting_the_same_version_is_idempotent_but_a_new_version_is_a_new_row(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    headers = seed.headers()
    first = await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "dpdp", "version": "v1"}, headers=headers
    )
    again = await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "dpdp", "version": "v1"}, headers=headers
    )
    assert again.json()["created"] is False
    assert again.json()["id"] == first.json()["id"]
    assert await count(database, Consent, seed.tenant_id) == 1

    bumped = await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "dpdp", "version": "v2"}, headers=headers
    )
    assert bumped.json()["created"] is True
    assert await count(database, Consent, seed.tenant_id) == 2

    listed = await privacy_client.get("/api/v1/me/consents", headers=headers)
    assert listed.status_code == 200
    assert {(c["kind"], c["version"]) for c in listed.json()} == {("dpdp", "v1"), ("dpdp", "v2")}


async def test_all_three_consent_kinds_are_accepted_and_junk_is_refused(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    for kind in ("dpdp", "privacy_policy", "terms"):
        r = await privacy_client.post(
            "/api/v1/me/consents", json={"kind": kind, "version": "v1"}, headers=seed.headers()
        )
        assert r.status_code == 201, (kind, r.text)
    bad = await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "cookies", "version": "v1"}, headers=seed.headers()
    )
    assert bad.status_code == 422


async def test_every_tenant_role_may_record_its_own_consent(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    for role in (Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER):
        r = await privacy_client.post(
            "/api/v1/me/consents",
            json={"kind": "terms", "version": "v1"},
            headers=seed.headers(role),
        )
        assert r.status_code == 201, (role, r.text)


# --- data-principal requests --------------------------------------------------------------


async def test_access_request_answers_with_the_users_own_rows_and_an_sla_date(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    headers = seed.headers()
    await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "dpdp", "version": "v1"}, headers=headers
    )
    await privacy_client.patch("/api/v1/me", json={"name": "Asha"}, headers=headers)

    r = await privacy_client.post(
        "/api/v1/me/data-requests", json={"kind": "access"}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "access"
    assert body["status"] == "done"
    assert body["overdue"] is False
    created = datetime.fromisoformat(body["created_at"])
    due = datetime.fromisoformat(body["sla_due_at"])
    assert timedelta(days=29, hours=23) < due - created < timedelta(days=30, hours=1)

    data = body["data"]
    assert data["user"]["email"] == seed.email
    assert data["user"]["name"] == "Asha"
    assert [c["kind"] for c in data["consents"]] == ["dpdp"]
    assert [m["role"] for m in data["memberships"]] == ["tenant_owner"]
    assert any(row["action"] == "me.update" for row in data["audit_log"])
    # the access request itself is in the export
    assert [d["kind"] for d in data["data_requests"]] == ["access"]


async def test_correction_and_erasure_requests_stay_open_on_the_sla_clock(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    headers = seed.headers()
    for kind in ("correction", "erasure"):
        r = await privacy_client.post(
            "/api/v1/me/data-requests",
            json={"kind": kind, "note": f"please {kind} my row"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "received"
        assert r.json()["completed_at"] is None
        assert r.json()["details"]["note"] == f"please {kind} my row"
        assert r.json()["data"] is None

    listed = await privacy_client.get("/api/v1/me/data-requests", headers=headers)
    assert listed.status_code == 200
    assert {row["kind"] for row in listed.json()} == {"correction", "erasure"}
    assert all(row["overdue"] is False for row in listed.json())


async def test_tenant_wide_kinds_are_refused_on_the_self_service_route(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    for kind in ("tenant_export", "tenant_delete"):
        r = await privacy_client.post(
            "/api/v1/me/data-requests", json={"kind": kind}, headers=seed.headers()
        )
        assert r.status_code == 422, (kind, r.text)
        assert r.json()["detail"]["error"] == "unsupported_kind"


async def test_a_users_requests_are_not_visible_to_another_member(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    other = uuid.uuid4()
    await privacy_client.post(
        "/api/v1/me/data-requests", json={"kind": "correction"}, headers=seed.headers()
    )
    r = await privacy_client.get(
        "/api/v1/me/data-requests",
        headers=auth_headers(
            user_id=other, tenant_id=seed.tenant_id, role=Role.WRITER, email="other@example.com"
        ),
    )
    assert r.status_code == 200
    assert r.json() == []


# --- tenant export -------------------------------------------------------------------------


async def _seed_content(database: Database, seed: Seed, storage_router: StorageRouter) -> str:
    """A profile, a usage row and one real object in storage."""
    async with database.owner_session() as session:
        tenant = await session.get(Tenant, seed.tenant_id)
        assert tenant is not None
        profile = CompanyProfile(
            tenant_id=seed.tenant_id, region=tenant.region, legal_name="Erasure Test LLC"
        )
        session.add(profile)
        session.add(
            UsageLedger(tenant_id=seed.tenant_id, metric="profiles", quantity=1, period="lifetime")
        )
        file_id = uuid.uuid4()
        key = f"tenants/{seed.tenant_id}/files/{file_id}.txt"
        storage = storage_router.for_region(tenant.data_residency)
        stored = await storage.put(key, b"secret capability statement", "text/plain")
        session.add(
            File(
                id=file_id,
                tenant_id=seed.tenant_id,
                filename="capability.txt",
                extension="txt",
                kind="text",
                content_type="text/plain",
                size_bytes=len(b"secret capability statement"),
                sha256=stored.sha256,
                region=tenant.data_residency,
                bucket=stored.bucket,
                key=key,
                uploaded_by=seed.user_id,
            )
        )
    return key


async def test_tenant_export_zips_every_table_and_file(
    privacy_client: httpx.AsyncClient,
    privacy_app: FastAPI,
    privacy_settings: Settings,
    database: Database,
) -> None:
    seed = await seed_tenant(database)
    storage_router = StorageRouter(privacy_settings)
    await _seed_content(database, seed, storage_router)

    r = await privacy_client.post("/api/v1/tenant/export", headers=seed.headers())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["kind"] == "tenant_export"
    assert body["status"] == "done"
    assert body["scheduling"] == "inline"
    assert body["result_file_id"]
    assert body["details"]["file_count"] == 1
    assert body["details"]["row_counts"]["company_profiles"] == 1
    assert body["details"]["row_counts"]["usage_ledger"] == 1
    # a ready signed URL for the owner (scheme depends on the backend: local:// in tests)
    assert body["details"]["key"] in body["details"]["url"]
    assert "signature=" in body["details"]["url"] and "expires=" in body["details"]["url"]

    async with database.owner_session() as session:
        tenant = await session.get(Tenant, seed.tenant_id)
        assert tenant is not None
        export_file = await session.get(File, uuid.UUID(body["result_file_id"]))
        assert export_file is not None
        assert export_file.content_type == "application/zip"
        assert export_file.key == body["details"]["key"]
    storage = storage_router.for_region(tenant.data_residency)
    archive = zipfile.ZipFile(io.BytesIO(await storage.get(export_file.key)))
    names = set(archive.namelist())
    assert "manifest.json" in names
    for table in tenant_tables():
        assert f"tables/{table.name}.json" in names, table.name
    profiles = json.loads(archive.read("tables/company_profiles.json"))
    assert profiles[0]["legal_name"] == "Erasure Test LLC"
    blob = next(n for n in names if n.startswith("files/"))
    assert archive.read(blob) == b"secret capability statement"
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["tenant_id"] == str(seed.tenant_id)
    assert manifest["files_missing_from_storage"] == []


async def test_export_requires_the_tenant_owner(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    for role in (Role.BID_MANAGER, Role.WRITER, Role.REVIEWER, Role.VIEWER):
        for route in ("/api/v1/tenant/export", "/api/v1/tenant/delete"):
            r = await privacy_client.post(route, headers=seed.headers(role))
            assert r.status_code == 403, (role, route, r.text)


# --- tenant erasure -----------------------------------------------------------------------------


async def test_tenant_delete_empties_every_tenant_table_but_keeps_the_audit_trail(
    privacy_client: httpx.AsyncClient,
    privacy_settings: Settings,
    database: Database,
) -> None:
    seed = await seed_tenant(database)
    keeper = await seed_tenant(database)  # a second tenant that must be untouched
    storage_router = StorageRouter(privacy_settings)
    key = await _seed_content(database, seed, storage_router)
    await _seed_content(database, keeper, storage_router)
    await privacy_client.post(
        "/api/v1/me/consents", json={"kind": "dpdp", "version": "v1"}, headers=seed.headers()
    )
    audit_before = await count(database, AuditLog, seed.tenant_id)
    assert audit_before > 0

    r = await privacy_client.post("/api/v1/tenant/delete", headers=seed.headers())
    assert r.status_code == 202, r.text
    assert r.json()["kind"] == "tenant_delete"
    assert r.json()["scheduling"] == "inline"

    # every tenant-scoped table in information_schema is empty for this tenant ...
    async with database.owner_engine.connect() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
                        "ORDER BY table_name"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert tables, "expected tenant-scoped tables"
        leftovers = {}
        for table in tables:
            remaining = (
                await conn.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE tenant_id = :t'),
                    {"t": seed.tenant_id},
                )
            ).scalar_one()
            if table == "audit_log":
                assert remaining >= audit_before + 1, "the audit trail must survive an erasure"
            elif remaining:
                leftovers[table] = remaining
    assert leftovers == {}, f"rows left behind after erasure: {leftovers}"
    # ... and the ORM's own list agrees
    for table in erasable_tables():
        assert table.name != "audit_log"

    # the deletion itself is audited
    actions = [a.action for a in await _rows_of(database, AuditLog, seed.tenant_id)]
    assert "privacy.tenant_delete" in actions

    # objects are gone from storage
    async with database.owner_session() as session:
        tenant = await session.get(Tenant, seed.tenant_id)
        assert tenant is not None
        assert tenant.deleted_at is not None
    storage = storage_router.for_region(tenant.data_residency)
    assert await storage.exists(key) is False

    # the other tenant is untouched
    assert await count(database, CompanyProfile, keeper.tenant_id) == 1
    assert await count(database, File, keeper.tenant_id) == 1


async def test_deleting_an_already_deleted_tenant_is_a_conflict(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    first = await privacy_client.post("/api/v1/tenant/delete", headers=seed.headers())
    assert first.status_code == 202
    again = await privacy_client.post("/api/v1/tenant/delete", headers=seed.headers())
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "tenant_deleted"
    export = await privacy_client.post("/api/v1/tenant/export", headers=seed.headers())
    assert export.status_code == 409


async def test_erasure_removes_the_users_left_without_any_membership(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    await privacy_client.get("/api/v1/me", headers=seed.headers())
    await privacy_client.post("/api/v1/tenant/delete", headers=seed.headers())
    async with database.owner_engine.connect() as conn:
        remaining = (
            await conn.execute(
                text("SELECT count(*) FROM users WHERE id = :u"), {"u": seed.user_id}
            )
        ).scalar_one()
    assert remaining == 0


async def test_export_and_delete_write_a_data_request_row_with_its_sla(
    privacy_client: httpx.AsyncClient, database: Database
) -> None:
    seed = await seed_tenant(database)
    r = await privacy_client.post("/api/v1/tenant/export", headers=seed.headers())
    assert r.status_code == 202
    async with database.owner_session() as session:
        row = (
            await session.execute(
                select(DataRequest).where(DataRequest.tenant_id == seed.tenant_id)
            )
        ).scalar_one()
    assert row.kind == "tenant_export"
    assert row.status == "done"
    assert row.completed_at is not None
    assert row.result_file_id is not None
    assert timedelta(days=29) < row.sla_due_at - row.created_at < timedelta(days=31)
