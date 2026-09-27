"""Row-level security helpers for milestone migrations.

Usage inside a migration::

    from migrations.rls import enable_rls, grant_app
    op.create_table("things", ..., sa.Column("tenant_id", UUID, ...))
    grant_app(op, "things")
    enable_rls(op, "things")

Policies key on current_setting('app.tenant_id', true)::uuid, which
app.core.db sets with SET LOCAL semantics on every transaction. FORCE makes the
policy apply to the owner too; the app role additionally has NOBYPASSRLS.
"""

from __future__ import annotations

import os
from typing import Any

APP_ROLE = os.environ.get("BIDRADAR_APP_ROLE", "bidradar_app")
# NULLIF: a reverted SET LOCAL leaves '' (not NULL) on a pooled connection.
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
DEFAULT_POLICY = "tenant_isolation"


def grant_app(op: Any, table: str, privileges: str = "SELECT, INSERT, UPDATE, DELETE") -> None:
    """Grant DML on `table` to the application role (never ownership)."""
    op.execute(f'GRANT {privileges} ON TABLE "{table}" TO {APP_ROLE}')


def revoke_app(op: Any, table: str, privileges: str) -> None:
    op.execute(f'REVOKE {privileges} ON TABLE "{table}" FROM {APP_ROLE}')


def enable_rls_expr(
    op: Any,
    table: str,
    using: str,
    *,
    check: str | None = None,
    policy: str = DEFAULT_POLICY,
    command: str = "ALL",
) -> None:
    """Enable + force RLS on `table` with a custom policy expression."""
    check = using if check is None else check
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY {policy} ON "{table}" FOR {command} USING ({using}) WITH CHECK ({check})'
    )


def enable_rls(op: Any, table: str, column: str = "tenant_id") -> None:
    """Standard tenant policy: rows visible/writable only when column = app.tenant_id."""
    enable_rls_expr(op, table, f"{column} = {TENANT_EXPR}")


def add_policy(
    op: Any,
    table: str,
    policy: str,
    *,
    command: str = "ALL",
    using: str | None = None,
    check: str | None = None,
) -> None:
    """Add an extra (permissive) policy to a table that already has RLS enabled."""
    parts = [f'CREATE POLICY {policy} ON "{table}" FOR {command}']
    if using is not None:
        parts.append(f"USING ({using})")
    if check is not None:
        parts.append(f"WITH CHECK ({check})")
    op.execute(" ".join(parts))


def disable_rls(op: Any, table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
