"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.api.audit_middleware import AuditMiddleware
from app.api.middleware import RequestIdMiddleware
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.ratelimit import FixedWindowLimiter
from app.logging import configure_logging


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=settings.is_production)
    yield


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
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
