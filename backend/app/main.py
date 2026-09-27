"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.adapters.registry import load_builtin_adapters
from app.agents.llm import llm_from_settings
from app.api import health
from app.api.audit_middleware import AuditMiddleware
from app.api.middleware import RequestIdMiddleware
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.db import get_database
from app.core.plan import PlanLimitExceeded
from app.core.ratelimit import FixedWindowLimiter
from app.logging import configure_logging
from app.services.billing import providers_from_settings
from app.services.embeddings import embeddings_from_settings
from app.services.enrichment import install_enrichment
from app.services.events import get_event_bus
from app.services.opportunity_embeddings import install_opportunity_embeddings
from app.services.scanner import scanner_from_settings
from app.services.sources import sync_sources_on_startup
from app.services.storage import StorageRouter


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=settings.is_production)
    # One `sources` row per registered adapter (SPEC 10.2); best-effort, never fatal.
    load_builtin_adapters()
    await sync_sources_on_startup()
    # summary_ai on opportunity.created/amended, only when an LLM is configured (M2-13)
    install_enrichment(settings, get_database(), app.state.storage_router, get_event_bus())
    # opportunities.embedding on the same events, after the summary (M1-12 / M4)
    install_opportunity_embeddings(settings, get_event_bus(), embeddings=app.state.embeddings)
    yield


async def _plan_limit_handler(_: Request, exc: PlanLimitExceeded) -> JSONResponse:
    """SPEC section 3: limits are enforced server-side; exceeding one is 402 Payment Required."""
    return JSONResponse(
        status_code=status.HTTP_402_PAYMENT_REQUIRED, content={"detail": exc.as_dict()}
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="BidRadar API",
        version=__version__,
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.auth_limiter = FixedWindowLimiter(
        limit=settings.auth_rate_limit_per_minute, window_seconds=60
    )
    # Storage per residency region and the virus scanner; tests swap these on app.state.
    app.state.storage_router = StorageRouter(settings)
    app.state.scanner = scanner_from_settings(settings)
    # LLM client for pipeline runs executed in-process (None without a key); tests inject
    # a FakeLLM here. agent_services is built lazily from storage_router + scanner.
    app.state.llm = llm_from_settings(settings)
    # embedding provider (Voyage | fake) for the knowledge base and autofill (M1-12)
    app.state.embeddings = embeddings_from_settings(settings)
    # billing providers (Stripe for us, Razorpay for in); tests install fakes on app.state
    app.state.billing_providers = providers_from_settings(settings)
    # add_middleware wraps outward: the LAST added is the outermost. Final order:
    # RequestId (outermost) -> CORS -> Audit -> routes.
    app.add_middleware(AuditMiddleware, trust_proxy=settings.trust_proxy_headers)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(PlanLimitExceeded, _plan_limit_handler)  # type: ignore[arg-type]
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
