"""Company-profile child tables (SPEC 4.2-4.5). Every row is tenant-scoped (RLS) and
belongs to one profile (FK cascade)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.notice_types import TeamingRole
from app.core.profile_fields import CertificationKind, CodeScheme, DeliveryModel, KeywordKind
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import _values
from app.models.types import EncryptedString

CertificationKindEnum = Enum(CertificationKind, name="certification_kind", values_callable=_values)
CodeSchemeEnum = Enum(CodeScheme, name="code_scheme", values_callable=_values)
KeywordKindEnum = Enum(KeywordKind, name="keyword_kind", values_callable=_values)
DeliveryModelEnum = Enum(DeliveryModel, name="delivery_model", values_callable=_values)
TeamingRoleEnum = Enum(TeamingRole, name="teaming_relationship", values_callable=_values)


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


class ProfileCode(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """NAICS / PSC / ALN (US) and GeM / India category (IN) codes the company sells under."""

    __tablename__ = "profile_codes"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "scheme", "code", name="uq_profile_codes_profile_scheme_code"
        ),
    )

    scheme: Mapped[CodeScheme] = mapped_column(CodeSchemeEnum, nullable=False)
    code: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class ProfileKeyword(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Weighted include keywords and exclusion keywords (scoring and noise filter)."""

    __tablename__ = "profile_keywords"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "kind", "term", name="uq_profile_keywords_profile_kind_term"
        ),
    )

    kind: Mapped[KeywordKind] = mapped_column(KeywordKindEnum, nullable=False)
    term: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), nullable=False, server_default=text("1.0")
    )


class ServiceLine(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Structured service lines: 150-word description, differentiators, tools, delivery."""

    __tablename__ = "service_lines"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    differentiators: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    tools: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    delivery_model: Mapped[DeliveryModel | None] = mapped_column(DeliveryModelEnum)


class TeamingPartner(UUIDPrimaryKeyMixin, TenantMixin, ProfileChildMixin, TimestampMixin, Base):
    """Known partners with their UEI/PAN and capabilities (SPEC 4.4 teaming)."""

    __tablename__ = "teaming_partners"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    relationship: Mapped[TeamingRole] = mapped_column(TeamingRoleEnum, nullable=False)
    uei: Mapped[str | None] = mapped_column(String(12))
    pan: Mapped[str | None] = mapped_column(EncryptedString)
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    website: Mapped[str | None] = mapped_column(String(500))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    notes: Mapped[str | None] = mapped_column(Text)
