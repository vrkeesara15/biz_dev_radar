"""Roles and the permission matrix from SPEC section 3. Pure logic."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_OWNER = "tenant_owner"
    BID_MANAGER = "bid_manager"
    WRITER = "writer"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


# Higher rank means broader tenant-scoped authority. platform_admin is NOT ranked
# above tenant roles on purpose: it has no tenant-data access without support access.
ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 10,
    Role.REVIEWER: 20,
    Role.WRITER: 30,
    Role.BID_MANAGER: 40,
    Role.TENANT_OWNER: 50,
    Role.PLATFORM_ADMIN: 0,
}

_OWNER = frozenset({Role.TENANT_OWNER})
_MANAGER_UP = frozenset({Role.TENANT_OWNER, Role.BID_MANAGER})
_WRITER_UP = frozenset({Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER})
_REVIEWER_UP = frozenset({Role.TENANT_OWNER, Role.BID_MANAGER, Role.WRITER, Role.REVIEWER})
_ALL_TENANT = frozenset(_REVIEWER_UP | {Role.VIEWER})
_PLATFORM = frozenset({Role.PLATFORM_ADMIN})

PERMISSIONS: dict[str, frozenset[Role]] = {
    # tenant owner
    "tenant.billing": _OWNER,
    "tenant.users": _OWNER,
    "tenant.integrations": _OWNER,
    "tenant.retention": _OWNER,
    "profile.edit": _OWNER,
    # bid manager
    "search.configure": _MANAGER_UP,
    "alerts.configure": _MANAGER_UP,
    "pursuit.stage": _MANAGER_UP,
    "pursuit.assign": _MANAGER_UP,
    "pursuit.decide": _MANAGER_UP,
    "package.approve": _MANAGER_UP,
    # writer / SME
    "draft.edit": _WRITER_UP,
    "agent.answer": _WRITER_UP,
    "profile.upload_evidence": _WRITER_UP,
    # reviewer
    "draft.comment": _REVIEWER_UP,
    "section.approve": _REVIEWER_UP,
    # everyone in the tenant
    "dashboard.view": _ALL_TENANT,
    "opportunity.view": _ALL_TENANT,
    "draft.view": _REVIEWER_UP,
    # platform admin only
    "admin.tenants": _PLATFORM,
    "admin.plans": _PLATFORM,
    "admin.sources": _PLATFORM,
    "admin.settings": _PLATFORM,
    "admin.health": _PLATFORM,
    "admin.support_access": _PLATFORM,
}

ADMIN_ACTION_PREFIX = "admin."


def parse_role(value: str) -> Role:
    """Parse a role string; raises ValueError with a helpful message."""
    try:
        return Role(value)
    except ValueError as exc:
        allowed = ", ".join(r.value for r in Role)
        raise ValueError(f"unknown role {value!r}; expected one of: {allowed}") from exc


def can(role: Role | str, action: str) -> bool:
    """True if `role` may perform `action`. Unknown actions are denied."""
    parsed = role if isinstance(role, Role) else parse_role(role)
    allowed = PERMISSIONS.get(action)
    if allowed is None:
        return False
    return parsed in allowed


def is_admin_action(action: str) -> bool:
    return action.startswith(ADMIN_ACTION_PREFIX)


def at_least(role: Role, minimum: Role) -> bool:
    """Tenant-role hierarchy check. platform_admin never satisfies a tenant minimum."""
    if role is Role.PLATFORM_ADMIN or minimum is Role.PLATFORM_ADMIN:
        return role is minimum
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def roles_at_least(minimum: Role) -> frozenset[Role]:
    return frozenset(r for r in Role if at_least(r, minimum))
