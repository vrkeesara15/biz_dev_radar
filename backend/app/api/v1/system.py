from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Region, Settings, get_settings

router = APIRouter(prefix="/system", tags=["system"])


class SystemInfo(BaseModel):
    version: str
    region: Region
    environment: str


@router.get("/info", response_model=SystemInfo)
async def system_info(settings: Annotated[Settings, Depends(get_settings)]) -> SystemInfo:
    """Public, non-tenant metadata used by the front end (region badge, version)."""
    return SystemInfo(
        version=settings.app_version, region=settings.region, environment=settings.app_env
    )
