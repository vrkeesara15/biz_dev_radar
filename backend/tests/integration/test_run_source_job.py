"""M2-16: python -m app.jobs.run_source, adapter.failing after > 2 failures, admin routes."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from app.adapters import registry
from app.core.db import Database
from app.core.roles import Role
from app.jobs import run_source as job
from app.models import Source, SourceRun
from app.services.events import ADAPTER_FAILING, EventBus, Recorder
from app.services.source_runner import run_source
from sqlalchemy import select

from tests.adapters.fixture_adapter import FixtureAdapter, raw_record
from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _records() -> list[Any]:
    return [
        raw_record("j-1", title="Job notice one", posted_at=NOW - timedelta(days=1)),
        raw_record("j-2", title="Job notice two", posted_at=NOW - timedelta(hours=1)),
    ]


async def test_run_source_job_syncs_sources_and_writes_a_run(database: Database) -> None:
    adapter = FixtureAdapter(_records())
    with registry.temporarily(FixtureAdapter):
        summary = await job.run_source_job("fixture", database=database, adapter=adapter, now=NOW)
    assert summary["source_id"] == "fixture" and summary["status"] == "ok"
    assert summary["fetched"] == 2 and summary["upserted"] == 2 and summary["mode"] == "inline"
    async with database.session(None) as session:
        run = await session.get(SourceRun, uuid.UUID(summary["run_id"]))
        assert run is not None and run.status == "ok" and run.upserted == 2
        source = await session.get(Source, "fixture")
        assert source is not None and source.watermark_at is not None


async def test_adapter_failing_event_after_more_than_two_consecutive_failures(
    database: Database,
) -> None:
    bus, rec = EventBus(), Recorder()
    bus.subscribe("*", rec)
    with registry.temporarily(FixtureAdapter):
        async with database.session(None) as session:
            from app.services.sources import sync_sources

            await sync_sources(session)
        for attempt in range(1, 5):
            adapter = FixtureAdapter(fail_fetch=RuntimeError(f"portal down {attempt}"))
            async with database.session(None) as session:
                result = await run_source(session, adapter, now=NOW, bus=bus)
            assert result.status == "failing"
            failing = rec.named(ADAPTER_FAILING)
            # 1st and 2nd failures: no event; 3rd and 4th: one event each
            assert len(failing) == max(0, attempt - 2), attempt
        payload = rec.named(ADAPTER_FAILING)[0].payload
        assert payload["source_id"] == "fixture" and payload["consecutive_failures"] == 3
        assert "portal down 3" in payload["message"]
        # a good run resets the counter
        async with database.session(None) as session:
            ok = await run_source(session, FixtureAdapter(_records()), now=NOW, bus=bus)
        assert ok.status == "ok"
        async with database.session(None) as session:
            source = await session.get(Source, "fixture")
            assert source is not None and source.consecutive_failures == 0
        assert len(rec.named(ADAPTER_FAILING)) == 2


def test_cli_main_reports_and_exit_codes(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(
        job,
        "run_source_sync",
        lambda sid, mode="cli": {"source_id": sid, "status": "ok", "mode": mode},
    )
    assert job.main(["grants_gov"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"source_id": "grants_gov", "status": "ok", "mode": "cli"}
    monkeypatch.setattr(
        job, "run_source_sync", lambda sid, mode="cli": {"source_id": sid, "status": "failing"}
    )
    assert job.main(["grants_gov"]) == 1

    def boom(sid: str, mode: str = "cli") -> dict[str, Any]:
        raise registry.AdapterNotFoundError(sid)

    monkeypatch.setattr(job, "run_source_sync", boom)
    assert job.main(["nope"]) == 2


def test_enqueue_returns_none_when_no_broker_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import celery_app as celery_module
    from kombu.exceptions import OperationalError

    def unreachable(*args: Any, **kwargs: Any) -> Any:
        raise OperationalError("Error 111 connecting to broker")

    monkeypatch.setattr(celery_module.run_source_task, "apply_async", unreachable)
    assert job.enqueue_run("sam_opps") is None
    monkeypatch.setattr(
        celery_module.run_source_task,
        "apply_async",
        lambda *a, **k: type("R", (), {"id": "task-123"})(),
    )
    assert job.enqueue_run("sam_opps") == "task-123"


@pytest.fixture()
async def admin(api_client: httpx.AsyncClient, database: Database) -> dict[str, Any]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(
            session, slug=f"admin-{uuid.uuid4().hex[:6]}"
        )
        other, other_user, _ = await create_tenant_with_owner(session)
    return {
        "client": api_client,
        "headers": auth_headers(user_id=user.id, tenant_id=tenant.id, role=Role.PLATFORM_ADMIN),
        "owner_headers": auth_headers(
            user_id=other_user.id, tenant_id=other.id, role=Role.TENANT_OWNER
        ),
    }


async def test_admin_sources_lists_health_and_recent_runs(
    admin: dict[str, Any], database: Database
) -> None:
    client, headers = admin["client"], admin["headers"]
    with registry.temporarily(FixtureAdapter):
        # one ok run and one failing run for the fixture source
        async with database.session(None) as session:
            from app.services.sources import sync_sources

            await sync_sources(session)
        async with database.session(None) as session:
            await run_source(session, FixtureAdapter(_records()), now=NOW)
        async with database.session(None) as session:
            await run_source(
                session,
                FixtureAdapter(fail_fetch=RuntimeError("down")),
                now=NOW + timedelta(hours=1),
            )
        resp = await client.get("/api/v1/admin/sources", headers=headers)
    assert resp.status_code == 200, resp.text
    by_id = {row["source_id"]: row for row in resp.json()}
    assert {"sam_opps", "grants_gov", "usaspending", "sam_awards", "fixture"} <= set(by_id)
    fixture = by_id["fixture"]
    assert fixture["health_status"] == "failing" and fixture["consecutive_failures"] == 1
    assert fixture["last_status"] == "failing" and fixture["watermark_at"]
    assert [r["status"] for r in fixture["runs"]] == ["failing", "ok"]  # newest first
    assert fixture["runs"][0]["last_error"] == "down" and fixture["runs"][1]["upserted"] == 2
    sam = by_id["sam_opps"]
    assert sam["schedule"] == "*/30 * * * *" and sam["registered"] and sam["enabled"]
    assert sam["runs"] == [] and sam["health_status"] == "ok"
    assert (
        await client.get("/api/v1/admin/sources", headers=admin["owner_headers"])
    ).status_code == 403
    assert (await client.get("/api/v1/admin/sources")).status_code == 401


async def test_admin_run_endpoint_queues_or_runs_inline(
    admin: dict[str, Any], database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = admin["client"], admin["headers"]
    queued: list[str] = []
    monkeypatch.setattr(job, "enqueue_run", lambda source_id: queued.append(source_id) or "task-9")
    with registry.temporarily(FixtureAdapter):
        resp = await client.post("/api/v1/admin/sources/fixture/run", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "source_id": "fixture",
            "mode": "queued",
            "task_id": "task-9",
            "result": None,
        }
        assert queued == ["fixture"]

        # broker unreachable -> inline fallback; explicit inline -> never touches the broker
        monkeypatch.setattr(job, "enqueue_run", lambda source_id: None)
        monkeypatch.setattr(
            job, "default_adapter_factory", lambda source_id: FixtureAdapter(_records())
        )
        resp = await client.post("/api/v1/admin/sources/fixture/run", headers=headers)
        assert resp.status_code == 200 and resp.json()["mode"] == "inline"
        assert resp.json()["result"]["status"] == "ok" and resp.json()["result"]["upserted"] == 2

        monkeypatch.setattr(
            job, "enqueue_run", lambda source_id: pytest.fail("inline must not enqueue")
        )
        resp = await client.post(
            "/api/v1/admin/sources/fixture/run", json={"inline": True}, headers=headers
        )
        assert resp.status_code == 200 and resp.json()["mode"] == "inline"
        assert resp.json()["result"]["upserted"] == 0  # unchanged second ingest

        # role and validation
        assert (
            await client.post("/api/v1/admin/sources/fixture/run", headers=admin["owner_headers"])
        ).status_code == 403
        assert (
            await client.post("/api/v1/admin/sources/nope/run", headers=headers)
        ).status_code == 404
    async with database.session(None) as session:
        runs = (
            (await session.execute(select(SourceRun).where(SourceRun.source_id == "fixture")))
            .scalars()
            .all()
        )
    assert len(runs) == 2
