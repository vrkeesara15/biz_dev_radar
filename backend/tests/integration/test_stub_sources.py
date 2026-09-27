"""M2-17: stubs get sources rows (disabled), cannot be run, show as not_implemented."""

import uuid
from typing import Any

import httpx
import pytest
from app.core.db import Database
from app.core.roles import Role
from app.jobs.run_source import run_source_job
from app.models import Source
from app.services.sources import sync_sources
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner


async def test_sync_creates_disabled_rows_and_run_refuses(database: Database) -> None:
    async with database.session(None) as session:
        await sync_sources(session)
        rows = {r.source_id: r for r in (await session.execute(select(Source))).scalars().all()}
    for sid in ("defense_gov_awards", "sled_generic", "ireps", "defproc", "highergov", "bidassist"):
        assert sid in rows and rows[sid].enabled is False
    assert rows["ireps"].region.value == "in" and rows["highergov"].schedule == "0 */2 * * *"
    with pytest.raises(RuntimeError, match="disabled"):
        await run_source_job("ireps", database=database)


async def test_admin_listing_and_run_endpoint_for_stubs(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, slug=f"adm-{uuid.uuid4().hex[:6]}"
        )
    headers = auth_headers(user_id=user.id, tenant_id=tenant.id, role=Role.PLATFORM_ADMIN)
    resp = await api_client.get("/api/v1/admin/sources", headers=headers)
    assert resp.status_code == 200
    by_id: dict[str, Any] = {row["source_id"]: row for row in resp.json()}
    assert by_id["tendertiger"]["enabled"] is False and by_id["tendertiger"]["registered"] is True
    assert by_id["defproc"]["runs"] == []
    resp = await api_client.post(
        "/api/v1/admin/sources/defproc/run", json={"inline": True}, headers=headers
    )
    assert resp.status_code == 409 and "disabled" in resp.text
