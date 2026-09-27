"""Uploaded objects (SPEC 4.5 files, 11 upload rules). One row per stored object; the
object key is built by app.core.paths.tenant_file_key and the bucket follows the tenant's
data residency. profile_files (M1-05) and later documents reference files.id."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import Region
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.tenancy import RegionEnum


class File(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "files"

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(8), nullable=False)
    # canonical sniffed kind (pdf, docx, xlsx, pptx, png, jpeg, text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[Region] = mapped_column(RegionEnum, nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    scan_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'clean'")
    )
    scanner: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'noop'"))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
