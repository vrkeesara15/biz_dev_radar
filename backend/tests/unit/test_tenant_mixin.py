"""M0-04: TenantMixin adds tenant_id uuid NOT NULL indexed."""

import uuid

from app.models.base import NAMING_CONVENTION, Base, TenantMixin, UUIDPrimaryKeyMixin
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _ScratchBase(DeclarativeBase):
    pass


class _Widget(UUIDPrimaryKeyMixin, TenantMixin, _ScratchBase):
    __tablename__ = "scratch_widget"
    name: Mapped[str] = mapped_column(String(50))


def test_tenant_mixin_column() -> None:
    col = _Widget.__table__.c.tenant_id
    assert isinstance(col.type, UUID)
    assert col.type.as_uuid is True
    assert col.nullable is False
    assert col.index is True
    assert any(col.name in ix.columns for ix in _Widget.__table__.indexes)
    id_col = _Widget.__table__.c.id
    assert isinstance(id_col.default.arg(None), uuid.UUID)
    assert id_col.server_default is not None


def test_base_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION
