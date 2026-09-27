"""Versioned business API. Every business route is mounted under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    files,
    me,
    notification_prefs,
    opportunities,
    profiles,
    pursuits,
    system,
)

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(system.router)
api_router.include_router(me.router)
api_router.include_router(notification_prefs.router)
api_router.include_router(admin.router)
api_router.include_router(files.router)
api_router.include_router(profiles.router)
api_router.include_router(opportunities.router)
api_router.include_router(pursuits.router)
