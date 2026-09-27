"""SQLAlchemy models. Import every module here so Alembic and tests see all tables."""

from app.models.audit import AuditLog
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.billing import PlanLimit, UsageLedger
from app.models.files import File
from app.models.profile import CompanyProfile
from app.models.profile_items import (
    Certification,
    ProfileCode,
    ProfileKeyword,
    ServiceLine,
    TeamingPartner,
)
from app.models.profile_proof import (
    BoilerplateBlock,
    Insurance,
    PastPerformance,
    Personnel,
    ProfileFile,
    RateCardEntry,
    Registration,
    Vehicle,
)
from app.models.tenancy import Membership, Tenant, User

__all__ = [
    "AuditLog",
    "Base",
    "BoilerplateBlock",
    "Certification",
    "CompanyProfile",
    "File",
    "Insurance",
    "Membership",
    "PastPerformance",
    "Personnel",
    "PlanLimit",
    "ProfileCode",
    "ProfileFile",
    "ProfileKeyword",
    "RateCardEntry",
    "Registration",
    "ServiceLine",
    "TeamingPartner",
    "Tenant",
    "TenantMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "UsageLedger",
    "User",
    "Vehicle",
]
