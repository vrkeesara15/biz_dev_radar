"""SQLAlchemy models. Import every module here so Alembic and tests see all tables."""

from app.models.agents import AgentRun, AgentStep
from app.models.audit import AuditLog
from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.billing import BillingCustomer, BillingEventRecord, PlanLimit, UsageLedger
from app.models.compliance import ComplianceItem, PursuitArtifact
from app.models.files import File
from app.models.integrations import Integration, IntegrationKind
from app.models.knowledge import KBChunk
from app.models.matching import Match, MatchBand
from app.models.notifications import (
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDelivery,
)
from app.models.notify import UserNotificationPrefs
from app.models.opportunities import (
    AwardsEnrichment,
    DocumentChunk,
    Opportunity,
    OpportunityDocument,
    OpportunityVersion,
)
from app.models.privacy import Consent, DataRequest
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
from app.models.push import PushSubscription
from app.models.requirements import Requirement
from app.models.sources import Source, SourceRun
from app.models.spend import AgencySpendStat
from app.models.support import SupportAccessGrant
from app.models.tenancy import Membership, Tenant, User

__all__ = [
    "AgencySpendStat",
    "AgentRun",
    "AgentStep",
    "AuditLog",
    "AwardsEnrichment",
    "Base",
    "BillingCustomer",
    "BillingEventRecord",
    "BoilerplateBlock",
    "Certification",
    "CompanyProfile",
    "ComplianceItem",
    "Consent",
    "DataRequest",
    "DeliveryChannel",
    "DeliveryStatus",
    "DocumentChunk",
    "File",
    "Insurance",
    "Integration",
    "IntegrationKind",
    "KBChunk",
    "Match",
    "MatchBand",
    "Membership",
    "Notification",
    "NotificationDelivery",
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
    "PursuitArtifact",
    "PushSubscription",
    "RateCardEntry",
    "Registration",
    "Requirement",
    "ServiceLine",
    "Source",
    "SourceRun",
    "SupportAccessGrant",
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
