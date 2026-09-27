"""agency_spend_stats: obligations by agency x sub-agency x NAICS x PSC x fiscal year.

Global table (no tenant_id, no RLS) rebuilt weekly from USAspending (SPEC 5.2: "who buys
what we sell"). Matching reads it by the profile's NAICS/PSC codes.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class AgencySpendStat(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agency_spend_stats"
    __table_args__ = (
        UniqueConstraint(
            "agency",
            "sub_agency",
            "naics",
            "psc",
            "fiscal_year",
            name="uq_agency_spend_stats_key",
        ),
    )

    agency: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Empty string (not NULL) for missing parts so the unique key holds.
    sub_agency: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    naics: Mapped[str] = mapped_column(String(16), nullable=False, server_default="", index=True)
    psc: Mapped[str] = mapped_column(String(16), nullable=False, server_default="", index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    obligations: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    award_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="usaspending")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
