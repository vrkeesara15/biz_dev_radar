"""M2-01: sources rows, source_runs and the runner over the FixtureAdapter."""

from datetime import UTC, datetime, timedelta

import pytest
from app.adapters.registry import temporarily
from app.core.db import Database
from app.models import Source, SourceRun
from app.services import sources as source_svc
from app.services.source_runner import run_source
from sqlalchemy import select, text

from tests.adapters.fixture_adapter import FixtureAdapter, raw_record

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


@pytest.fixture()
def registered():  # type: ignore[no-untyped-def]
    with temporarily(FixtureAdapter):
        yield


async def test_sync_sources_creates_one_row_per_registered_adapter(
    database: Database, registered: None
) -> None:
    async with database.session(None) as session:
        created = await source_svc.sync_sources(session)
    assert "fixture" in created
    async with database.session(None) as session:
        row = await session.get(Source, "fixture")
        assert row is not None
        assert row.region.value == "us"
        assert row.schedule == "*/30 * * * *"
        assert row.enabled is True
        assert row.watermark_at is None
        # idempotent; operator edits survive a restart
        row.enabled = False
        row.schedule = "stale"
    async with database.session(None) as session:
        assert await source_svc.sync_sources(session) == []
        row = await session.get(Source, "fixture")
        assert row is not None
        assert row.enabled is False, "enabled is operator-owned"
        assert row.schedule == "*/30 * * * *", "schedule follows the code"


async def test_app_startup_syncs_sources(database: Database, registered: None, app) -> None:  # type: ignore[no-untyped-def]
    async with app.router.lifespan_context(app):
        pass
    async with database.session(None) as session:
        assert await session.get(Source, "fixture") is not None


async def test_sources_tables_are_global_and_writable_by_app_role(
    database: Database, registered: None
) -> None:
    async with database.owner_engine.connect() as conn:
        for table in ("sources", "source_runs"):
            cols = set(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": table},
                    )
                )
                .scalars()
                .all()
            )
            assert "tenant_id" not in cols
            rls = (
                await conn.execute(
                    text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}
                )
            ).scalar()
            assert rls is False
    # No tenant context at all, app role: rows are visible and writable.
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        run = await source_svc.start_run(session, "fixture", now=NOW)
    async with database.session(None) as session:
        assert (await session.get(SourceRun, run.id)) is not None
        assert (await session.execute(select(Source))).scalars().one().source_id == "fixture"


async def test_run_source_end_to_end_records_run_and_watermark(
    database: Database, registered: None
) -> None:
    records = [
        raw_record("n1", title="One", posted_at=NOW - timedelta(days=5)),
        raw_record("n2", title="Two", posted_at=NOW - timedelta(days=1), docs=["https://x/a.pdf"]),
        raw_record("n3", title="Three", posted_at=NOW - timedelta(days=40)),  # before since
    ]
    adapter = FixtureAdapter(records)
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        result = await run_source(session, adapter, now=NOW, keep_records=True)
    assert result.status == "ok"
    assert result.since == NOW - timedelta(days=30)
    assert result.fetched == 2 and result.upserted == 2
    assert [r.external_id for r in result.records] == ["n1", "n2"]
    assert result.records[1].documents[0].file_name == "a.pdf"
    assert result.watermark == NOW - timedelta(days=1)
    async with database.session(None) as session:
        run = (await session.execute(select(SourceRun))).scalars().one()
        assert run.status == "ok" and run.fetched == 2 and run.upserted == 2
        assert run.errors == [] and run.finished_at is not None
        assert run.watermark == NOW - timedelta(days=1)
        source = await session.get(Source, "fixture")
        assert source is not None
        assert source.watermark_at == NOW - timedelta(days=1)
        assert source.last_status == "ok" and source.consecutive_failures == 0

    # Second run: since = watermark - 2 days, so only n2 is re-fetched (late-edit overlap).
    async with database.session(None) as session:
        result2 = await run_source(session, adapter, now=NOW)
    assert result2.since == NOW - timedelta(days=3)
    assert result2.fetched == 1
    assert adapter.calls[-1] == (NOW - timedelta(days=3), None)


async def test_run_source_records_per_record_errors_as_degraded(
    database: Database, registered: None
) -> None:
    records = [raw_record("good", posted_at=NOW), raw_record("bad", posted_at=NOW)]
    adapter = FixtureAdapter(records, bad_ids={"bad"})
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        result = await run_source(session, adapter, now=NOW)
    assert result.status == "degraded"
    assert result.fetched == 2 and result.upserted == 1
    assert result.errors[0]["external_id"] == "bad"
    assert result.errors[0]["type"] == "ValueError"
    async with database.session(None) as session:
        run = (await session.execute(select(SourceRun))).scalars().one()
        assert run.status == "degraded"
        assert run.errors[0]["message"] == "cannot normalise bad"
        source = await session.get(Source, "fixture")
        assert source is not None
        assert source.health_status == "degraded"
        assert source.health_message == "cannot normalise bad"


async def test_fetch_failure_marks_run_failing_and_keeps_watermark(
    database: Database, registered: None
) -> None:
    wm = NOW - timedelta(days=7)
    async with database.session(None) as session:
        await source_svc.sync_sources(session)
        (await source_svc.get_source(session, "fixture")).watermark_at = wm
    adapter = FixtureAdapter(fail_fetch=RuntimeError("boom"))
    async with database.session(None) as session:
        result = await run_source(session, adapter, now=NOW)
        result2 = await run_source(session, adapter, now=NOW)
    assert result.status == "failing" and result2.status == "failing"
    assert result.errors[0]["stage"] == "fetch" and result.errors[0]["message"] == "boom"
    async with database.session(None) as session:
        source = await session.get(Source, "fixture")
        assert source is not None
        assert source.watermark_at == wm
        assert source.consecutive_failures == 2
        assert source.health_status == "failing"
        runs = await source_svc.recent_runs(session, "fixture")
        assert [r.status for r in runs] == ["failing", "failing"]
