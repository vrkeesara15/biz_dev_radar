"""Versioned business API. Every business route is mounted under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    billing,
    files,
    integrations,
    me,
    me_notifications,
    notification_prefs,
    notifications,
    opportunities,
    privacy,
    profiles,
    system,
    webhooks,
)

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(system.router)
api_router.include_router(me.router)
api_router.include_router(me_notifications.router)
api_router.include_router(notification_prefs.router)
api_router.include_router(notifications.router)
api_router.include_router(admin.router)
api_router.include_router(files.router)
api_router.include_router(integrations.router)
api_router.include_router(profiles.router)
api_router.include_router(opportunities.router)
api_router.include_router(billing.router)
api_router.include_router(webhooks.router)
api_router.include_router(privacy.me_router)
api_router.include_router(privacy.tenant_router)
api_router.include_router(privacy.public_router)
