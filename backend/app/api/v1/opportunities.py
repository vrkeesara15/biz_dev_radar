"""Opportunities API (SPEC 10.3): search with filters and full-text query, detail with
versions and documents. Opportunities are GLOBAL (public notices), so any tenant role may
read them; every record carries source attribution and the portal disclaimer (SPEC 11).

    GET /api/v1/opportunities?q=&region=&type=&naics=&due_before=&min_score=&status=&page=
    GET /api/v1/opportunities/{opportunity_id}
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnClause, Select, String, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import selectinload

from app.api.deps import TENANT_ROLES, CurrentUser, TenantSessionDep, require_role
from app.core.attribution import portal_url, source_name
from app.core.config import Region
from app.core.disclaimers import VERIFY_ON_PORTAL, attribution_text, record_footer
from app.core.opportunity import NoticeType, OpportunityStatus
from app.models import Opportunity, OpportunityDocument, OpportunityVersion
from app.models.opportunities import FTS_EXPR

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
ReaderDep = Annotated[CurrentUser, Depends(require_role(*TENANT_ROLES))]

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25
FTS_CONFIG: ColumnClause[Any] = literal_column("'english'::regconfig")
# the exact expression of ix_opportunities_fts, so the planner can use the GIN index
FTS_VECTOR: ColumnClause[Any] = literal_column(FTS_EXPR)


class Attribution(BaseModel):
    source_id: str
    source_name: str
    source_url: str | None
    # SPEC 11 / M3-10: the ready-to-render line, identical in the API, exports and emails
    text: str
    footer: str


class OpportunityItem(BaseModel):
    id: uuid.UUID
    source_id: str
    external_id: str
    region: Region
    country: str
    currency: str
    notice_type: NoticeType
    status: OpportunityStatus
    title: str
    summary_ai: str | None
    solicitation_number: str | None
    buyer_org: str | None
    buyer_sub_org: str | None
    buyer_office: str | None
    naics: list[str]
    psc: list[str]
    aln: list[str]
    set_aside: str | None
    reservation: str | None
    estimated_value_min: Decimal | None
    estimated_value_max: Decimal | None
    estimated_value_min_usd: Decimal | None
    estimated_value_max_usd: Decimal | None
    posted_at: datetime | None
    response_due_at: datetime | None
    source_tz: str
    incumbent: str | None
    prior_award_value: Decimal | None
    prior_pop_end: date | None
    version: int
    parent_opportunity_id: uuid.UUID | None
    duplicate_of: uuid.UUID | None
    # the active profile's match score arrives with M4 (matches table); null until then
    match: dict[str, Any] | None = None
    attribution: Attribution
    disclaimer: str = VERIFY_ON_PORTAL


class OpportunityPage(BaseModel):
    items: list[OpportunityItem]
    total: int
    page: int
    page_size: int
    pages: int


class VersionOut(BaseModel):
    version: int
    diff: dict[str, Any]
    changes: list[str]
    created_at: datetime


class DocumentOut(BaseModel):
    id: uuid.UUID
    file_name: str | None
    url: str
    kind: str
    mime_type: str | None
    size: int | None
    pages: int | None
    status: str
    hash: str | None


class OpportunityDetail(OpportunityItem):
    description_text: str | None
    buyer_hierarchy: list[str]
    india_category: list[str]
    place_of_performance: dict[str, Any] | None
    emd_amount: Decimal | None
    tender_fee: Decimal | None
    questions_due_at: datetime | None
    prebid_meeting_at: datetime | None
    opening_at: datetime | None
    archive_at: datetime | None
    contacts: list[Any]
    eligibility: dict[str, Any]
    detail_status: str
    content_hash: str | None
    also_from: list[dict[str, Any]] = Field(default_factory=list)
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime
    versions: list[VersionOut]
    documents: list[DocumentOut]


def _attribution(row: Opportunity) -> Attribution:
    return Attribution(
        source_id=row.source_id,
        source_name=source_name(row.source_id),
        source_url=portal_url(row.source_id, row.source_url),
        text=attribution_text(row.source_id, row.source_url),
        footer=record_footer(row.source_id, row.source_url),
    )


ITEM_FIELDS = tuple(
    name
    for name in OpportunityItem.model_fields
    if name not in {"attribution", "disclaimer", "match"}
)


def item_out(row: Opportunity) -> OpportunityItem:
    return OpportunityItem(
        **{name: getattr(row, name) for name in ITEM_FIELDS},
        match=None,
        attribution=_attribution(row),
    )


def detail_out(row: Opportunity) -> OpportunityDetail:
    base = item_out(row).model_dump()
    extra_fields = (
        "description_text",
        "buyer_hierarchy",
        "india_category",
        "place_of_performance",
        "emd_amount",
        "tender_fee",
        "questions_due_at",
        "prebid_meeting_at",
        "opening_at",
        "archive_at",
        "contacts",
        "eligibility",
        "detail_status",
        "content_hash",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    return OpportunityDetail(
        **base,
        **{name: getattr(row, name) for name in extra_fields},
        also_from=list((row.extra or {}).get("also_from") or []),
        versions=[
            VersionOut(version=v.version, diff=v.diff, changes=v.changes, created_at=v.created_at)
            for v in row.versions
        ],
        documents=[
            DocumentOut(
                id=d.id,
                file_name=d.file_name,
                url=d.url,
                kind=d.kind,
                mime_type=d.mime_type,
                size=d.size,
                pages=d.pages,
                status=d.status,
                hash=d.hash,
            )
            for d in sorted(row.documents, key=lambda d: (d.created_at, d.url))
        ],
    )


def _split_csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def apply_filters(
    stmt: Select[Any],
    *,
    q: str | None,
    region: Region | None,
    notice_types: list[NoticeType],
    naics: list[str],
    due_before: datetime | None,
    statuses: list[OpportunityStatus],
    include_duplicates: bool,
) -> Select[Any]:
    if not include_duplicates:
        stmt = stmt.where(Opportunity.duplicate_of.is_(None))
    if q:
        stmt = stmt.where(FTS_VECTOR.op("@@")(func.websearch_to_tsquery(FTS_CONFIG, q)))
    if region is not None:
        stmt = stmt.where(Opportunity.region == region)
    if notice_types:
        stmt = stmt.where(Opportunity.notice_type.in_(notice_types))
    if naics:
        # typed array so the && operator matches the varchar[] column (and its GIN index)
        stmt = stmt.where(Opportunity.naics.overlap(cast(array(naics), ARRAY(String(16)))))
    if due_before is not None:
        stmt = stmt.where(Opportunity.response_due_at <= due_before)
    if statuses:
        stmt = stmt.where(Opportunity.status.in_(statuses))
    return stmt


def search_statement(
    *,
    q: str | None,
    region: Region | None,
    notice_types: list[NoticeType],
    naics: list[str],
    due_before: datetime | None,
    statuses: list[OpportunityStatus],
    include_duplicates: bool,
) -> Select[Any]:
    stmt = apply_filters(
        select(Opportunity),
        q=q,
        region=region,
        notice_types=notice_types,
        naics=naics,
        due_before=due_before,
        statuses=statuses,
        include_duplicates=include_duplicates,
    )
    if q:
        rank = func.ts_rank(FTS_VECTOR, func.websearch_to_tsquery(FTS_CONFIG, q))
        return stmt.order_by(rank.desc(), Opportunity.posted_at.desc().nulls_last())
    return stmt.order_by(
        Opportunity.response_due_at.asc().nulls_last(), Opportunity.posted_at.desc().nulls_last()
    )


def _parse_enums(raw: str | None, enum: type[Any], name: str) -> list[Any]:
    values = []
    for part in _split_csv(raw):
        try:
            values.append(enum(part))
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown {name} {part!r}; one of {[e.value for e in enum]}",
            ) from exc
    return values


@router.get("", response_model=OpportunityPage)
async def search_opportunities(
    session: TenantSessionDep,
    _user: ReaderDep,
    q: Annotated[str | None, Query(max_length=500, description="websearch syntax")] = None,
    region: Region | None = None,
    type: Annotated[str | None, Query(description="notice types, comma-separated")] = None,
    naics: Annotated[str | None, Query(description="NAICS codes, comma-separated")] = None,
    due_before: datetime | None = None,
    min_score: Annotated[
        int | None, Query(ge=0, le=100, description="accepted now, applied once matches exist (M4)")
    ] = None,
    status: Annotated[str | None, Query(description="statuses, comma-separated")] = None,
    include_duplicates: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> OpportunityPage:
    notice_types = _parse_enums(type, NoticeType, "notice type")
    statuses = _parse_enums(status, OpportunityStatus, "status")
    codes = _split_csv(naics)
    filters: dict[str, Any] = {
        "q": q,
        "region": region,
        "notice_types": notice_types,
        "naics": codes,
        "due_before": due_before,
        "statuses": statuses,
        "include_duplicates": include_duplicates,
    }
    total = (
        await session.execute(
            apply_filters(select(func.count()).select_from(Opportunity), **filters)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                search_statement(**filters).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return OpportunityPage(
        items=[item_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, -(-total // page_size)),
    )


@router.get("/{opportunity_id}", response_model=OpportunityDetail)
async def get_opportunity(
    opportunity_id: uuid.UUID, session: TenantSessionDep, _user: ReaderDep
) -> OpportunityDetail:
    row = (
        await session.execute(
            select(Opportunity)
            .options(selectinload(Opportunity.versions), selectinload(Opportunity.documents))
            .where(Opportunity.id == opportunity_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="opportunity not found")
    return detail_out(row)


__all__ = ["OpportunityDocument", "OpportunityVersion", "router", "search_statement"]
