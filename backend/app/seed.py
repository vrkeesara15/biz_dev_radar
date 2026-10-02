"""Seed the internal tenant and the platform admin (SPEC section 3). Idempotent.

    uv run python -m app.seed          # or: make seed

Runs on the owner role: the internal tenant does not exist yet, so no RLS context can
see it. Re-running updates nothing that already exists except the tenant's fixed
attributes (internal, enterprise, us).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import Region, Settings, get_settings
from app.core.db import Database, get_database
from app.core.plan import Plan
from app.core.roles import Role
from app.models import Membership, Tenant, User

INTERNAL_SLUG = "internal"
INTERNAL_NAME = "BidRadar (internal)"


@dataclass(frozen=True, slots=True)
class SeedResult:
    tenant_id: str
    user_id: str
    admin_email: str
    created_tenant: bool
    created_user: bool
    created_membership: bool

    def as_json(self) -> str:
        """One machine-readable line for a deploy script.

        The frontend's `BIDRADAR_DEV_TENANT_ID` (the OQ-11 membership stub) has to be
        the internal tenant's uuid, which only exists after this seed runs. Printing it
        as JSON on stdout means the Railway runbook can do
        `railway logs --service api | grep internal_tenant_id` instead of asking a
        human to copy a uuid out of prose. The key is `internal_tenant_id` rather than
        `tenant_id` so it is greppable in a log line that also mentions other ids.
        """
        return json.dumps(
            {
                "internal_tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "admin_email": self.admin_email,
                "created_tenant": self.created_tenant,
                "created_user": self.created_user,
                "created_membership": self.created_membership,
            },
            sort_keys=True,
        )

    def summary(self) -> str:
        return (
            f"internal tenant {self.tenant_id} ({'created' if self.created_tenant else 'exists'}); "
            f"platform admin {self.user_id} "
            f"({'created' if self.created_user else 'exists'}, membership "
            f"{'created' if self.created_membership else 'exists'})"
        )


async def seed(database: Database, admin_email: str) -> SeedResult:
    email = admin_email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("SEED_ADMIN_EMAIL must be a valid email address")
    async with database.owner_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == INTERNAL_SLUG))
        ).scalar_one_or_none()
        created_tenant = tenant is None
        if tenant is None:
            tenant = Tenant(
                name=INTERNAL_NAME,
                slug=INTERNAL_SLUG,
                region=Region.US,
                data_residency=Region.US,
                plan=Plan.ENTERPRISE,
                is_internal=True,
            )
            session.add(tenant)
        else:
            tenant.is_internal = True
            tenant.plan = Plan.ENTERPRISE
        await session.flush()

        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        created_user = user is None
        if user is None:
            user = User(email=email, name="Platform admin")
            session.add(user)
            await session.flush()

        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id, Membership.tenant_id == tenant.id
                )
            )
        ).scalar_one_or_none()
        created_membership = membership is None
        if membership is None:
            session.add(Membership(user_id=user.id, tenant_id=tenant.id, role=Role.PLATFORM_ADMIN))
        else:
            membership.role = Role.PLATFORM_ADMIN
        await session.flush()
        return SeedResult(
            tenant_id=str(tenant.id),
            user_id=str(user.id),
            admin_email=email,
            created_tenant=created_tenant,
            created_user=created_user,
            created_membership=created_membership,
        )


async def run(settings: Settings | None = None, database: Database | None = None) -> SeedResult:
    settings = settings or get_settings()
    return await seed(database or get_database(), settings.seed_admin_email)


def main() -> int:
    result = asyncio.run(run())
    # stdout is the JSON line a script parses; the prose goes to stderr so a pipe
    # into `jq` stays clean.
    sys.stderr.write(result.summary() + "\n")
    sys.stdout.write(result.as_json() + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
