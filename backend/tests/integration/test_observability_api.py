"""M7-05: the request context reaches the logs, and the instrumentation produces spans.

No collector and no Sentry project are involved: the OTel spans are captured with an
in-memory exporter attached to a provider this test owns, so the global tracer provider
(which must stay unset when OTEL_EXPORTER_OTLP_ENDPOINT is empty) is never touched.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import structlog
from app.api.deps import CurrentUserDep
from app.core.config import Settings
from app.core.context import get_tenant_id
from app.core.db import Database
from app.core.roles import Role
from app.logging import configure_logging
from app.main import create_app
from app.observability import instrument
from fastapi import FastAPI

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

log = structlog.get_logger("tests.observability")


@pytest.fixture()
def json_logs() -> Iterator[None]:
    configure_logging("INFO", json_output=True)
    yield
    configure_logging("INFO", json_output=False)


def _lines(captured: str) -> list[dict[str, Any]]:
    out = []
    for raw in captured.splitlines():
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                out.append(json.loads(raw))
            except ValueError:  # pragma: no cover - console noise
                continue
    return out


@pytest.fixture()
def logging_app(settings: Settings, fake_embeddings: Any) -> FastAPI:
    """A real app with one extra route that logs from inside the request."""
    application = create_app(settings)
    application.state.embeddings = fake_embeddings

    @application.get("/api/v1/system/_log_probe")
    async def _probe(user: CurrentUserDep) -> dict[str, str]:
        log.info("probe.handled", route="_log_probe")
        return {"tenant": str(user.tenant_id)}

    application.openapi_schema = None
    return application


async def test_request_log_lines_carry_request_id_tenant_id_and_user_id(
    logging_app: FastAPI,
    database: Database,
    clean_db: Any,
    json_logs: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=logging_app), base_url="http://test"
    ) as client:
        capsys.readouterr()
        r = await client.get(
            "/api/v1/system/_log_probe",
            headers=auth_headers(
                user_id=user.id, tenant_id=tenant.id, role=Role.WRITER, email=user.email
            ),
        )
    assert r.status_code == 200, r.text
    probe = next(
        line for line in _lines(capsys.readouterr().out) if line["event"] == "probe.handled"
    )
    assert probe["request_id"] == r.headers["X-Request-ID"]
    assert probe["tenant_id"] == str(tenant.id)
    assert probe["user_id"] == str(user.id)
    assert probe["route"] == "_log_probe"


async def test_the_principal_is_cleared_when_the_request_ends(
    logging_app: FastAPI,
    database: Database,
    clean_db: Any,
    json_logs: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=logging_app), base_url="http://test"
    ) as client:
        await client.get(
            "/api/v1/system/_log_probe",
            headers=auth_headers(user_id=user.id, tenant_id=tenant.id, email=user.email),
        )
    assert get_tenant_id() is None
    capsys.readouterr()
    log.info("after.request")
    line = next(x for x in _lines(capsys.readouterr().out) if x["event"] == "after.request")
    assert "tenant_id" not in line and "user_id" not in line


async def test_an_unauthenticated_request_logs_no_principal(
    logging_app: FastAPI, clean_db: Any, json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=logging_app), base_url="http://test"
    ) as client:
        r = await client.get("/api/v1/system/_log_probe")
    assert r.status_code == 401
    assert all("tenant_id" not in line for line in _lines(capsys.readouterr().out))


# --- OpenTelemetry spans -----------------------------------------------------------------


@pytest.fixture()
def span_exporter() -> Iterator[Any]:
    """A provider owned by this test; the global one stays untouched (see the unit test)."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter, provider
    exporter.clear()


async def test_fastapi_instrumentation_produces_a_span_per_request(
    settings: Settings, fake_embeddings: Any, span_exporter: Any, clean_db: Any
) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    exporter, provider = span_exporter
    app = create_app(settings)
    app.state.embeddings = fake_embeddings
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="healthz")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/system/info")).status_code == 200
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    names = [span.name for span in exporter.get_finished_spans()]
    assert any("/api/v1/system/info" in name for name in names), names


def test_instrument_wires_the_expected_libraries_per_component() -> None:
    """The API instruments SQLAlchemy and httpx; the worker adds Celery."""
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    try:
        assert instrument(component="api") == ["sqlalchemy", "httpx"]
        assert instrument(component="worker")[-1] == "celery"
    finally:
        for instrumentor in (
            CeleryInstrumentor(),
            HTTPXClientInstrumentor(),
            SQLAlchemyInstrumentor(),
        ):
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()


def test_sentry_client_is_inactive_without_a_dsn(settings: Settings) -> None:
    import sentry_sdk

    create_app(settings)
    client = sentry_sdk.get_client()
    assert not client.is_active(), "no SENTRY_DSN must leave the Sentry client inactive"


# --- Langfuse tags ----------------------------------------------------------------------


def test_langfuse_trace_is_tagged_with_the_tenant_and_pursuit() -> None:
    from app.agents.tracing import LangfuseTracer

    class _Gen:
        def end(self, **_kw: Any) -> None:
            return None

    class _Trace:
        def generation(self, **_kw: Any) -> _Gen:
            return _Gen()

    class _Client:
        def __init__(self) -> None:
            self.traces: list[dict[str, Any]] = []

        def trace(self, **kw: Any) -> _Trace:
            self.traces.append(kw)
            return _Trace()

    client = _Client()
    tracer = LangfuseTracer(client, settings=Settings(_env_file=None))  # type: ignore[call-arg]
    tenant = str(uuid.uuid4())
    pursuit = str(uuid.uuid4())
    tracer.run_started(run_id="r1", kind="bid_no_bid", tenant_id=tenant, pursuit_id=pursuit)
    assert client.traces[0]["tags"] == [f"tenant:{tenant}", "kind:bid_no_bid", f"pursuit:{pursuit}"]
    assert client.traces[0]["metadata"] == {
        "tenant_id": tenant,
        "kind": "bid_no_bid",
        "pursuit_id": pursuit,
    }
    tracer.run_started(run_id="r2", kind="summary_ai", tenant_id=tenant)
    assert client.traces[1]["tags"] == [f"tenant:{tenant}", "kind:summary_ai"]
