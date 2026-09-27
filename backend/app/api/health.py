from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    version: str


@router.get("/healthz", response_model=Health, include_in_schema=True)
async def healthz() -> Health:
    return Health(status="ok", version=__version__)
