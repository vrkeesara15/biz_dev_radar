"""Route factories for the cross-tenant harness (SPEC section 12).

Every route in app.openapi() must either appear in PUBLIC_ROUTES or have a factory
here keyed by (METHOD, path template). A factory receives the two-tenant context and
returns the RouteCall to make *as tenant B* using *tenant A's* ids. The harness then
fails if any 2xx body contains one of A's object ids.

Adding a route:
    FACTORIES[("POST", "/api/v1/things/{id}/pursue")] = lambda ctx: RouteCall(
        path_params={"id": ctx.a.ids["thing"]}, json={"note": "probe"}
    )
Extend build_context() when the new route needs an object owned by A.
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Region
from app.core.db import Database
from app.core.notice_types import TeamingRole
from app.core.profile_fields import CertificationKind, CodeScheme, KeywordKind
from app.core.roles import Role
from app.models import (
    AuditLog,
    Certification,
    CompanyProfile,
    File,
    ProfileCode,
    ProfileKeyword,
    ServiceLine,
    TeamingPartner,
    UsageLedger,
)

from tests.factories import create_tenant_with_owner

# The ONLY routes that may be exercised without a tenant: health, docs, public metadata,
# auth callbacks and provider webhooks (which authenticate by signature, not by tenant).
PUBLIC_ROUTES: list[tuple[str, str]] = [
    ("GET", "/healthz"),
    ("GET", "/api/v1/system/info"),
    ("*", "/api/v1/auth/*"),
    ("POST", "/api/v1/webhooks/*"),
]

OK_STATUSES = frozenset({200, 201, 202, 204})


def is_public(method: str, path: str) -> bool:
    return any(
        (m == "*" or m == method.upper()) and fnmatch.fnmatchcase(path, pattern)
        for m, pattern in PUBLIC_ROUTES
    )


@dataclass
class TenantCtx:
    id: uuid.UUID
    owner_id: uuid.UUID
    owner_email: str
    # name -> id string; every value must be absent from B's 2xx responses.
    ids: dict[str, str] = field(default_factory=dict)


@dataclass
class IsolationContext:
    a: TenantCtx
    b: TenantCtx


@dataclass(frozen=True)
class RouteCall:
    path_params: dict[str, Any] = field(default_factory=dict)
    json: Any = None
    params: dict[str, Any] | None = None
    # multipart parts, httpx style: {"file": (filename, bytes, content_type)}
    files: dict[str, Any] | None = None
    role: Role = Role.TENANT_OWNER
    # Statuses accepted when the same call is made by tenant A's own owner (sanity check
    # that the factory produces a well-formed request). Admin routes expect 403.
    owner_expect: frozenset[int] = OK_STATUSES


Factory = Callable[[IsolationContext], RouteCall]


def child_routes(
    name: str, seed_key: str, create_json: dict[str, Any], update_json: dict[str, Any]
) -> dict[tuple[str, str], Factory]:
    """The five CRUD routes of a profile sub-resource, probed with A's profile and A's row."""
    base = f"/api/v1/profiles/{{profile_id}}/{name}"

    def params(ctx: IsolationContext, with_item: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"profile_id": ctx.a.ids["profile"]}
        if with_item:
            out["item_id"] = ctx.a.ids[seed_key]
        return out

    return {
        ("GET", base): lambda ctx: RouteCall(path_params=params(ctx)),
        ("POST", base): lambda ctx: RouteCall(path_params=params(ctx), json=create_json),
        ("GET", base + "/{item_id}"): lambda ctx: RouteCall(path_params=params(ctx, True)),
        ("PUT", base + "/{item_id}"): lambda ctx: RouteCall(
            path_params=params(ctx, True), json=update_json
        ),
        ("DELETE", base + "/{item_id}"): lambda ctx: RouteCall(
            path_params=params(ctx, True), owner_expect=frozenset({204})
        ),
    }


FACTORIES: dict[tuple[str, str], Factory] = {
    ("GET", "/api/v1/me"): lambda ctx: RouteCall(),
    ("PATCH", "/api/v1/me"): lambda ctx: RouteCall(json={"name": "Isolation probe"}),
    ("GET", "/api/v1/admin/tenants"): lambda ctx: RouteCall(owner_expect=frozenset({403})),
    ("POST", "/api/v1/admin/tenants/{tenant_id}/support-access"): lambda ctx: RouteCall(
        path_params={"tenant_id": ctx.a.id},
        json={"reason": "isolation probe"},
        owner_expect=frozenset({403}),
    ),
    # --- profiles (M1-01)
    ("POST", "/api/v1/profiles"): lambda ctx: RouteCall(
        json={"region": "us", "legal_name": "Probe LLC"}
    ),
    ("GET", "/api/v1/profiles/{profile_id}"): lambda ctx: RouteCall(
        path_params={"profile_id": ctx.a.ids["profile"]}
    ),
    ("PUT", "/api/v1/profiles/{profile_id}"): lambda ctx: RouteCall(
        path_params={"profile_id": ctx.a.ids["profile"]}, json={"legal_name": "Renamed"}
    ),
    # --- profile sub-resources (M1-02..M1-05)
    **child_routes("codes", "code", {"scheme": "psc", "code": "D302"}, {"is_primary": True}),
    **child_routes(
        "keywords", "keyword", {"kind": "include", "term": "probe term"}, {"weight": "2.5"}
    ),
    **child_routes(
        "service-lines",
        "service_line",
        {"name": "Probe line", "description": "probe"},
        {"name": "Probe line 2"},
    ),
    **child_routes(
        "teaming-partners",
        "teaming_partner",
        {"name": "Probe Partners", "relationship": "sub"},
        {"relationship": "jv"},
    ),
    **child_routes(
        "certifications",
        "certification",
        {"kind": "8a", "cert_number": "PROBE-1"},
        {"cert_number": "PROBE-2"},
    ),
    # --- files (M1-11)
    ("POST", "/api/v1/files"): lambda ctx: RouteCall(
        files={"file": ("probe.txt", b"isolation probe", "text/plain")}
    ),
    ("GET", "/api/v1/files/{file_id}"): lambda ctx: RouteCall(
        path_params={"file_id": ctx.a.ids["file"]}
    ),
    ("GET", "/api/v1/files/{file_id}/url"): lambda ctx: RouteCall(
        path_params={"file_id": ctx.a.ids["file"]}
    ),
}


async def build_context(database: Database) -> IsolationContext:
    """Tenants A and B with one owner each, plus sample objects owned by A."""
    async with database.owner_session() as session:
        ta, ua, ma = await create_tenant_with_owner(session, slug=f"iso-a-{uuid.uuid4().hex[:6]}")
        tb, ub, mb = await create_tenant_with_owner(session, slug=f"iso-b-{uuid.uuid4().hex[:6]}")
        ledger = UsageLedger(tenant_id=ta.id, metric="profiles", quantity=1, period="lifetime")
        audit = AuditLog(tenant_id=ta.id, user_id=ua.id, action="isolation.seed")
        file_id = uuid.uuid4()
        file = File(
            id=file_id,
            tenant_id=ta.id,
            filename="a-capability.txt",
            extension="txt",
            kind="text",
            content_type="text/plain",
            size_bytes=5,
            sha256="0" * 64,
            region=Region.US,
            bucket="bidradar-us",
            key=f"tenants/{ta.id}/files/{file_id}.txt",
            uploaded_by=ua.id,
        )
        profile = CompanyProfile(
            tenant_id=ta.id,
            region=Region.US,
            legal_name="Alpha Federal LLC",
            uei="ALPHA1234567",
            ein="12-3456789",
        )
        session.add_all([ledger, audit, file, profile])
        await session.flush()
        certification = Certification(
            tenant_id=ta.id,
            profile_id=profile.id,
            kind=CertificationKind.EIGHT_A,
            cert_number="A-8A-0001",
        )
        code = ProfileCode(
            tenant_id=ta.id,
            profile_id=profile.id,
            scheme=CodeScheme.NAICS,
            code="541511",
            title="Custom Computer Programming Services",
            is_primary=True,
        )
        keyword = ProfileKeyword(
            tenant_id=ta.id,
            profile_id=profile.id,
            kind=KeywordKind.INCLUDE,
            term="alpha secret term",
        )
        service_line = ServiceLine(
            tenant_id=ta.id,
            profile_id=profile.id,
            name="Alpha Cloud Line",
            description="alpha desc",
        )
        partner = TeamingPartner(
            tenant_id=ta.id,
            profile_id=profile.id,
            name="Alpha Partner Corp",
            relationship=TeamingRole.SUB,
            uei="PARTNER12345",
            pan="ABCDE1234F",
        )
        session.add_all([certification, code, keyword, service_line, partner])
        await session.flush()
        a = TenantCtx(
            id=ta.id,
            owner_id=ua.id,
            owner_email=ua.email,
            ids={
                "tenant": str(ta.id),
                "tenant_slug": ta.slug,
                "owner_user": str(ua.id),
                "owner_email": ua.email,
                "membership": str(ma.id),
                "usage_ledger": str(ledger.id),
                "audit_log": str(audit.id),
                "file": str(file.id),
                "file_key": file.key,
                "profile": str(profile.id),
                "profile_legal_name": profile.legal_name,
                "profile_uei": "ALPHA1234567",
                "profile_ein": "12-3456789",
                "certification": str(certification.id),
                "certification_number": "A-8A-0001",
                "code": str(code.id),
                "keyword": str(keyword.id),
                "keyword_term": "alpha secret term",
                "service_line": str(service_line.id),
                "service_line_name": "Alpha Cloud Line",
                "teaming_partner": str(partner.id),
                "teaming_partner_name": "Alpha Partner Corp",
                "teaming_partner_uei": "PARTNER12345",
                "teaming_partner_pan": "ABCDE1234F",
            },
        )
        b = TenantCtx(
            id=tb.id,
            owner_id=ub.id,
            owner_email=ub.email,
            ids={"tenant": str(tb.id), "owner_user": str(ub.id), "membership": str(mb.id)},
        )
    return IsolationContext(a=a, b=b)
