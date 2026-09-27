"""Versioned business API. Every business route is mounted under /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import system

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(system.router)
