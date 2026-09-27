"""OpenTelemetry, Sentry and structured logging wiring (SPEC 10.1, M7-05).

    configure_observability(settings, app=app)        # API process (app/main.py)
    configure_observability(settings, component="worker")   # Celery worker

Everything here is opt-in: with an empty `OTEL_EXPORTER_OTLP_ENDPOINT` no tracer provider
is installed and no exporter thread starts, with an empty `SENTRY_DSN` Sentry is never
initialised, and with no Langfuse keys `agents.tracing` stays a NoopTracer. A process with
none of the three configured does exactly what it did before this module existed, which is
what `tests/unit/test_observability.py` asserts.

Traces cover the API (FastAPI routes), the database (SQLAlchemy), outbound HTTP (httpx,
so adapter fetches are spans) and Celery tasks (adapter runs, status rolls, exports).
Every span carries `service.name` (bidradar-api / bidradar-worker) and `deployment.region`
so Cloud Trace can separate the US and Indian deployments.

Sentry is configured with `send_default_pii=False` plus a `before_send` scrubber, because
the payloads this product handles (EIN, PAN, GSTIN, bid strategy) must never leave the
region in a crash report.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.core.config import Settings, get_settings
from app.core.context import get_request_id, get_tenant_id, get_user_id

log = structlog.get_logger(__name__)

API_SERVICE_NAME = "bidradar-api"
WORKER_SERVICE_NAME = "bidradar-worker"

# Header values and field names that must never reach Sentry, whatever else is scrubbed.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "stripe-signature",
        "x-razorpay-signature",
    }
)
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "token",
        "secret",
        "api_key",
        "authorization",
        "ein",
        "pan",
        "gstin",
        "tan",
        "bank_account",
        "ifsc",
        "email",
    }
)
REDACTED = "[redacted]"

# Value patterns worth catching even inside free text (a stack frame local, an error
# message). Deliberately conservative: an over-eager pattern makes crash reports useless.
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# same shapes as core.profile_fields validates, anchored on word boundaries instead
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[\w\-.=]+")

_MAX_SCRUB_DEPTH = 8


def scrub_text(value: str) -> str:
    """Mask the identifier shapes SPEC 11 calls out, in any string we are about to send."""
    value = BEARER_RE.sub(f"Bearer {REDACTED}", value)
    value = GSTIN_RE.sub(REDACTED, value)
    value = PAN_RE.sub(REDACTED, value)
    value = EIN_RE.sub(REDACTED, value)
    return EMAIL_RE.sub(REDACTED, value)


def scrub(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact sensitive keys and mask sensitive values."""
    if depth > _MAX_SCRUB_DEPTH:
        return value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if name in SENSITIVE_KEYS or name in SENSITIVE_HEADERS:
                out[key] = REDACTED
            else:
                out[key] = scrub(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [scrub(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub(item, depth=depth + 1) for item in value)
    if isinstance(value, str):
        return scrub_text(value)
    return value


def before_send(event: Any, _hint: Any = None) -> Any:
    """Sentry `before_send`: scrub the event and tag it with the request's tenant."""
    scrubbed: dict[str, Any] = scrub(dict(event))
    tags = dict(scrubbed.get("tags") or {})
    tenant_id = get_tenant_id()
    if tenant_id:
        tags["tenant_id"] = tenant_id
    request_id = get_request_id()
    if request_id:
        tags["request_id"] = request_id
    if tags:
        scrubbed["tags"] = tags
    # A user is identified by id only; the email is deliberately not attached.
    user_id = get_user_id()
    scrubbed["user"] = {"id": user_id} if user_id else None
    return scrubbed


# --- Sentry ------------------------------------------------------------------------------


def configure_sentry(settings: Settings) -> bool:
    """Initialise Sentry when SENTRY_DSN is set. Returns whether it was enabled."""
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - the dependency is declared
        log.warning("observability.sentry_unavailable")
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=before_send,
    )
    sentry_sdk.set_tag("region", settings.region.value)
    log.info("observability.sentry_enabled", environment=settings.app_env)
    return True


# --- OpenTelemetry ------------------------------------------------------------------------


def _resource(settings: Settings, component: str) -> Any:
    from opentelemetry.sdk.resources import Resource

    return Resource.create(
        {
            "service.name": API_SERVICE_NAME if component == "api" else WORKER_SERVICE_NAME,
            "service.version": settings.app_version,
            "deployment.environment": settings.app_env,
            "deployment.region": settings.region.value,
        }
    )


def configure_tracing(settings: Settings, *, app: Any = None, component: str = "api") -> bool:
    """Install the OTLP tracer provider and the instrumentations. No endpoint, no-op."""
    if not settings.otel_exporter_otlp_endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover - the dependency is declared
        log.warning("observability.otel_unavailable")
        return False
    provider = TracerProvider(resource=_resource(settings, component))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
            )
        )
    )
    trace.set_tracer_provider(provider)
    instrument(app=app, component=component)
    log.info(
        "observability.tracing_enabled",
        component=component,
        endpoint=settings.otel_exporter_otlp_endpoint,
    )
    return True


def instrument(*, app: Any = None, component: str = "api") -> list[str]:
    """Instrument FastAPI, SQLAlchemy, httpx and Celery; returns what was wired."""
    wired: list[str] = []
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
        wired.append("fastapi")
    for name, module_path, cls_name in (
        ("sqlalchemy", "opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("httpx", "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("celery", "opentelemetry.instrumentation.celery", "CeleryInstrumentor"),
    ):
        if name == "celery" and component != "worker":
            continue
        module = __import__(module_path, fromlist=[cls_name])
        instrumentor = getattr(module, cls_name)()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument()
        wired.append(name)
    return wired


# --- entry point ----------------------------------------------------------------------------


def configure_observability(
    settings: Settings | None = None, *, app: Any = None, component: str = "api"
) -> dict[str, bool]:
    """Wire tracing, error reporting and log context for one process.

    Returns which of the three are live, so the caller (and the tests) can see at a
    glance what an environment actually has switched on.
    """
    settings = settings or get_settings()
    enabled = {
        "tracing": configure_tracing(settings, app=app, component=component),
        "sentry": configure_sentry(settings),
        "langfuse": bool(settings.langfuse_public_key and settings.langfuse_secret_key),
    }
    log.debug("observability.configured", component=component, **enabled)
    return enabled
