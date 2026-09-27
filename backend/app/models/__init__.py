"""SQLAlchemy models. Import every module here so Alembic and tests see all tables."""

from app.models.agents import AgentRun, AgentStep
from app.models.audit import AuditLog
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.billing import PlanLimit, UsageLedger
from app.models.files import File
from app.models.notify import UserNotificationPrefs
from app.models.opportunities import (
    AwardsEnrichment,
    DocumentChunk,
    Opportunity,
    OpportunityDocument,
    OpportunityVersion,
)
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
from app.models.pursuit import Pursuit
from app.models.requirements import Requirement
from app.models.sources import Source, SourceRun
from app.models.spend import AgencySpendStat
from app.models.tenancy import Membership, Tenant, User

__all__ = [
    "AgencySpendStat",
    "AgentRun",
    "AgentStep",
    "AuditLog",
    "AwardsEnrichment",
    "Base",
    "BoilerplateBlock",
    "Certification",
    "CompanyProfile",
    "DocumentChunk",
    "File",
    "Insurance",
    "Membership",
    "Opportunity",
    "OpportunityDocument",
    "OpportunityVersion",
    "PastPerformance",
    "Personnel",
    "PlanLimit",
    "ProfileCode",
    "ProfileFile",
    "ProfileKeyword",
    "Pursuit",
    "RateCardEntry",
    "Registration",
    "Requirement",
    "ServiceLine",
    "Source",
    "SourceRun",
    "TeamingPartner",
    "Tenant",
    "TenantMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "UsageLedger",
    "User",
    "UserNotificationPrefs",
    "Vehicle",
]
