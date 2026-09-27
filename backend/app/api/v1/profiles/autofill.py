"""POST /profiles/{profile_id}/autofill (SPEC 10.3): suggestions from the website, a
capability statement and the SAM.gov entity record; nothing is written to the profile."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator, model_validator

from app.api.deps import SettingsDep, StorageRouterDep, TenantSessionDep
from app.api.v1.profiles.common import EditorDep, get_profile, reject_region_foreign
from app.core.db import get_database
from app.core.profile_fields import normalize_uei
from app.services.audit import AuditHint
from app.services.autofill import Autofiller, AutofillResponse

router = APIRouter(prefix="/{profile_id}/autofill", tags=["profiles:autofill"])


class AutofillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    website_url: HttpUrl | None = None
    capability_file_id: uuid.UUID | None = None
    uei: str | None = None

    @field_validator("uei")
    @classmethod
    def _uei(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_uei(value)  # ValueError -> 422: 12 alphanumeric characters

    @field_validator("website_url")
    @classmethod
    def _http_only(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and value.scheme not in ("http", "https"):
            raise ValueError("website_url must be http(s)")
        return value

    @model_validator(mode="after")
    def _at_least_one(self) -> AutofillRequest:
        if not (self.website_url or self.capability_file_id or self.uei):
            raise ValueError("provide website_url, capability_file_id or uei")
        return self


@router.post("", response_model=AutofillResponse, name="autofill_profile")
async def autofill_profile(
    profile_id: uuid.UUID,
    body: AutofillRequest,
    user: EditorDep,
    session: TenantSessionDep,
    request: Request,
    settings: SettingsDep,
    storage_router: StorageRouterDep,
) -> AutofillResponse:
    profile = await get_profile(session, profile_id)
    if body.uei:
        reject_region_foreign(profile.region, {"uei"})
    llm = getattr(request.app.state, "llm", None)
    autofiller = Autofiller(
        settings=settings, database=get_database(), llm=llm, storage=storage_router
    )
    try:
        response = await autofiller.run(
            session,
            profile,
            website_url=str(body.website_url) if body.website_url else None,
            capability_file_id=body.capability_file_id,
            uei=body.uei,
        )
    except Exception as exc:  # never a 500 with a half-built answer; nothing was written
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"autofill failed: {type(exc).__name__}"
        ) from exc
    sources: list[str] = []
    if body.website_url:
        sources.append("website")
    if body.capability_file_id:
        sources.append("capability_pdf")
    if body.uei:
        sources.append("uei")
    meta: dict[str, Any] = {
        "sources": sources,
        "suggestions": len(response.suggestions),
        "warnings": len(response.warnings),
    }
    request.state.audit = AuditHint(
        action="profile.autofill",
        object_type="company_profile",
        object_id=str(profile.id),
        meta=meta,
    )
    return response
