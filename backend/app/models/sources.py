"""sources and source_runs (SPEC 10.2). Global tables: no tenant_id, no RLS."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import Region
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import RegionEnum


class Source(TimestampMixin, Base):
    """One row per registered adapter; holds the incremental watermark and last health."""

    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[Region] = mapped_column(RegionEnum, nullable=False)
    schedule: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    # last_posted_at of the newest record ingested; next fetch starts 2 days earlier.
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(String(512))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    health_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'ok'")
    )
    health_message: Mapped[str | None] = mapped_column(String(1000))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    runs: Mapped[list[SourceRun]] = relationship(back_populates="source")


class SourceRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_runs"

    source_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # running | ok | degraded | failing
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'running'")
    )
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    upserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    errors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(String(512))

    source: Mapped[Source] = relationship(back_populates="runs")
