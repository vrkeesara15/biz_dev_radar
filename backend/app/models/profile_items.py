"""Company-profile child tables (SPEC 4.2-4.5). Every row is tenant-scoped (RLS) and
belongs to one profile (FK cascade)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.profile_fields import CertificationKind
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import _values

CertificationKindEnum = Enum(CertificationKind, name="certification_kind", values_callable=_values)


class ProfileChildMixin:
    @declared_attr
    @classmethod
    def profile_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("company_profiles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class Certification(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Socio-economic certs (8a, HUBZone, WOSB...) and security/compliance attestations."""

    __tablename__ = "certifications"

    kind: Mapped[CertificationKind] = mapped_column(CertificationKindEnum, nullable=False)
    cert_number: Mapped[str | None] = mapped_column(String(100))
    issued_by: Mapped[str | None] = mapped_column(String(200))
    # CMMC level, FCL level (confidential/secret/top_secret), CMMI level, ISO edition...
    level: Mapped[str | None] = mapped_column(String(32))
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
