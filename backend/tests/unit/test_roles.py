"""M0-01: permission matrix from SPEC section 3."""

import pytest
from app.core.roles import (
    PERMISSIONS,
    Role,
    at_least,
    can,
    is_admin_action,
    parse_role,
    roles_at_least,
)


def test_role_values() -> None:
    assert [r.value for r in Role] == [
        "platform_admin",
        "tenant_owner",
        "bid_manager",
        "writer",
        "reviewer",
        "viewer",
    ]


@pytest.mark.parametrize(
    ("role", "action", "expected"),
    [
        (Role.TENANT_OWNER, "tenant.billing", True),
        (Role.BID_MANAGER, "tenant.billing", False),
        (Role.BID_MANAGER, "pursuit.decide", True),
        (Role.WRITER, "pursuit.decide", False),
        (Role.WRITER, "draft.edit", True),
        (Role.REVIEWER, "draft.edit", False),
        (Role.REVIEWER, "section.approve", True),
        (Role.VIEWER, "dashboard.view", True),
        (Role.VIEWER, "draft.view", False),
        (Role.PLATFORM_ADMIN, "admin.tenants", True),
        (Role.PLATFORM_ADMIN, "draft.view", False),
        (Role.TENANT_OWNER, "admin.tenants", False),
    ],
)
def test_matrix(role: Role, action: str, expected: bool) -> None:
    assert can(role, action) is expected


def test_can_accepts_strings_and_denies_unknown_actions() -> None:
    assert can("tenant_owner", "tenant.users")
    assert not can(Role.TENANT_OWNER, "does.not.exist")
    with pytest.raises(ValueError, match="unknown role"):
        can("superuser", "tenant.users")
    assert parse_role("viewer") is Role.VIEWER


def test_platform_admin_has_no_tenant_data_permissions() -> None:
    tenant_actions = [a for a in PERMISSIONS if not is_admin_action(a)]
    assert tenant_actions
    assert all(not can(Role.PLATFORM_ADMIN, a) for a in tenant_actions)
    admin_actions = [a for a in PERMISSIONS if is_admin_action(a)]
    assert all(PERMISSIONS[a] == frozenset({Role.PLATFORM_ADMIN}) for a in admin_actions)


def test_hierarchy() -> None:
    assert at_least(Role.TENANT_OWNER, Role.VIEWER)
    assert at_least(Role.WRITER, Role.WRITER)
    assert not at_least(Role.REVIEWER, Role.WRITER)
    assert not at_least(Role.PLATFORM_ADMIN, Role.VIEWER)
    assert not at_least(Role.TENANT_OWNER, Role.PLATFORM_ADMIN)
    assert at_least(Role.PLATFORM_ADMIN, Role.PLATFORM_ADMIN)
    assert roles_at_least(Role.BID_MANAGER) == frozenset({Role.TENANT_OWNER, Role.BID_MANAGER})
