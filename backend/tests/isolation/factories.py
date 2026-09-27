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
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Region
from app.core.db import Database
from app.core.notice_types import TeamingRole
from app.core.opportunity import NoticeType
from app.core.profile_fields import (
    BoilerplateKind,
    CertificationKind,
    CodeScheme,
    InsuranceKind,
    KeywordKind,
    PerformanceRole,
    ProfileFileKind,
    RateUnit,
    RegistrationKind,
)
from app.core.roles import Role
from app.models import (
    AuditLog,
    BillingCustomer,
    BillingEventRecord,
    BoilerplateBlock,
    Certification,
    Comment,
    CompanyProfile,
    Consent,
    DataRequest,
    Draft,
    DraftVersion,
    File,
    Insurance,
    Integration,
    Notification,
    Opportunity,
    PastPerformance,
    Personnel,
    ProfileCode,
    ProfileFile,
    ProfileKeyword,
    Pursuit,
    PursuitArtifact,
    PushSubscription,
    RateCardEntry,
    Registration,
    ServiceLine,
    Task,
    TeamingPartner,
    UsageLedger,
    UserNotificationPrefs,
    Vehicle,
)

from tests.factories import create_tenant_with_owner

# The ONLY routes that may be exercised without a tenant: health, docs, public metadata,
# auth callbacks and provider webhooks (which authenticate by signature, not by tenant).
PUBLIC_ROUTES: list[tuple[str, str]] = [
    ("GET", "/healthz"),
    ("GET", "/api/v1/system/info"),
    ("*", "/api/v1/auth/*"),
    ("POST", "/api/v1/webhooks/*"),
    # M4-09: one-click actions clicked from email/Slack/Teams carry a signed token (HS256,
    # AUTH_SECRET) that names the tenant, user and notification; there is no bearer session.
    # tests/integration/test_notify_core.py proves a foreign or tampered token is 401.
    ("GET", "/api/v1/notifications/actions/*"),
    # M4-10: the CAN-SPAM unsubscribe link in every email footer. Same signed-token scheme
    # (tenant + user + category); GET is the footer link, POST the RFC 8058 one-click
    # target named by List-Unsubscribe-Post. tests/integration/test_notify_email.py proves
    # a tampered token is 401 and that the opt-out lands on the right tenant's prefs row.
    ("GET", "/api/v1/notifications/unsubscribe/*"),
    ("POST", "/api/v1/notifications/unsubscribe/*"),
    # M4-11: Slack's interactive callback. Slack has no bearer token; the request proves
    # itself twice (our signed action token names the tenant, X-Slack-Signature proves it
    # came from Slack). tests/integration/test_integrations_api.py covers both halves.
    ("POST", "/api/v1/integrations/slack/actions"),
    # M7-07: the published privacy notice (grievance officer, sub-processors, versions)
    ("GET", "/api/v1/privacy"),
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
    # GLOBAL (public) objects owned by no tenant, e.g. an opportunity: any tenant may read
    # them, so their ids are deliberately NOT in a.ids (a 200 for B is correct there).
    shared: dict[str, str] = field(default_factory=dict)


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


Payload = dict[str, Any] | Callable[["IsolationContext"], dict[str, Any]]


def _payload(payload: Payload, ctx: IsolationContext) -> dict[str, Any]:
    return payload(ctx) if callable(payload) else payload


def child_routes(
    name: str,
    seed_key: str,
    create_json: Payload,
    update_json: Payload,
    role: Role = Role.TENANT_OWNER,
) -> dict[tuple[str, str], Factory]:
    """The five CRUD routes of a profile sub-resource, probed with A's profile and A's row.
    Payloads may be callables so a probe can reference A's ids (e.g. a file id)."""
    base = f"/api/v1/profiles/{{profile_id}}/{name}"

    def params(ctx: IsolationContext, with_item: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"profile_id": ctx.a.ids["profile"]}
        if with_item:
            out["item_id"] = ctx.a.ids[seed_key]
        return out

    return {
        ("GET", base): lambda ctx: RouteCall(path_params=params(ctx), role=role),
        ("POST", base): lambda ctx: RouteCall(
            path_params=params(ctx), json=_payload(create_json, ctx), role=role
        ),
        ("GET", base + "/{item_id}"): lambda ctx: RouteCall(
            path_params=params(ctx, True), role=role
        ),
        ("PUT", base + "/{item_id}"): lambda ctx: RouteCall(
            path_params=params(ctx, True), json=_payload(update_json, ctx), role=role
        ),
        ("DELETE", base + "/{item_id}"): lambda ctx: RouteCall(
            path_params=params(ctx, True), owner_expect=frozenset({204}), role=role
        ),
    }


FACTORIES: dict[tuple[str, str], Factory] = {
    ("GET", "/api/v1/me"): lambda ctx: RouteCall(),
    ("PATCH", "/api/v1/me"): lambda ctx: RouteCall(json={"name": "Isolation probe"}),
    # --- in-app bell and web push (M4-12): everything is scoped to the caller's own user
    ("GET", "/api/v1/me/notifications"): lambda ctx: RouteCall(),
    ("POST", "/api/v1/me/notifications/{notification_id}/read"): lambda ctx: RouteCall(
        path_params={"notification_id": ctx.a.ids["notification"]},
        owner_expect=frozenset({200}),
    ),
    ("POST", "/api/v1/me/notifications/read-all"): lambda ctx: RouteCall(),
    ("POST", "/api/v1/me/push-subscriptions"): lambda ctx: RouteCall(
        json={
            "endpoint": f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}",
            "keys": {"p256dh": "probe-key", "auth": "probe-auth"},
        },
        owner_expect=frozenset({201}),
    ),
    ("DELETE", "/api/v1/me/push-subscriptions"): lambda ctx: RouteCall(
        json={"endpoint": ctx.a.ids["push_subscription_endpoint"]},
        # A's owner owns the seeded endpoint (204); B cannot see it at all (404)
        owner_expect=frozenset({204}),
    ),
    ("GET", "/api/v1/me/notification-prefs"): lambda ctx: RouteCall(),
    ("PUT", "/api/v1/me/notification-prefs"): lambda ctx: RouteCall(json={"min_score_instant": 80}),
    # --- admin console (M7-08): platform_admin only, so a tenant owner always gets 403
    ("GET", "/api/v1/admin/tenants"): lambda ctx: RouteCall(owner_expect=frozenset({403})),
    ("GET", "/api/v1/admin/tenants/{tenant_id}"): lambda ctx: RouteCall(
        path_params={"tenant_id": ctx.a.id}, owner_expect=frozenset({403})
    ),
    ("PATCH", "/api/v1/admin/tenants/{tenant_id}"): lambda ctx: RouteCall(
        path_params={"tenant_id": ctx.a.id}, json={"plan": "pro"}, owner_expect=frozenset({403})
    ),
    ("GET", "/api/v1/admin/tenants/{tenant_id}/audit-log"): lambda ctx: RouteCall(
        path_params={"tenant_id": ctx.a.id}, owner_expect=frozenset({403})
    ),
    ("GET", "/api/v1/admin/usage"): lambda ctx: RouteCall(owner_expect=frozenset({403})),
    ("GET", "/api/v1/admin/health"): lambda ctx: RouteCall(owner_expect=frozenset({403})),
    ("GET", "/api/v1/admin/sources/{source_id}/runs"): lambda ctx: RouteCall(
        path_params={"source_id": "sam_opps"}, owner_expect=frozenset({403})
    ),
    # --- admin sources (M2-16): platform_admin only; tenant owners get 403
    ("GET", "/api/v1/admin/sources"): lambda ctx: RouteCall(owner_expect=frozenset({403})),
    ("POST", "/api/v1/admin/sources/{source_id}/run"): lambda ctx: RouteCall(
        path_params={"source_id": "sam_opps"},
        json={"inline": True},
        owner_expect=frozenset({403}),
    ),
    ("POST", "/api/v1/admin/tenants/{tenant_id}/support-access"): lambda ctx: RouteCall(
        path_params={"tenant_id": ctx.a.id},
        json={"reason": "isolation probe"},
        owner_expect=frozenset({403}),
    ),
    # --- profiles (M1-01)
    ("GET", "/api/v1/profiles"): lambda ctx: RouteCall(),
    ("POST", "/api/v1/profiles"): lambda ctx: RouteCall(
        json={"region": "us", "legal_name": "Probe LLC"}
    ),
    ("GET", "/api/v1/profiles/{profile_id}"): lambda ctx: RouteCall(
        path_params={"profile_id": ctx.a.ids["profile"]}
    ),
    ("PUT", "/api/v1/profiles/{profile_id}"): lambda ctx: RouteCall(
        path_params={"profile_id": ctx.a.ids["profile"]}, json={"legal_name": "Renamed"}
    ),
    # --- autofill (M1-10): read-only suggestions; B probing A's profile gets 404. Without a
    # SAM key the UEI path answers 200 with a warning and no network call.
    ("POST", "/api/v1/profiles/{profile_id}/autofill"): lambda ctx: RouteCall(
        path_params={"profile_id": ctx.a.ids["profile"]},
        json={"uei": "ALPHA1234567"},
        role=Role.BID_MANAGER,
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
    # --- proof (M1-05); past performance and personnel probed as WRITER (allowed role)
    **child_routes(
        "past-performance",
        "past_performance",
        {"title": "Probe PP", "customer": "Probe Agency", "role": "prime", "scope": "probe"},
        {"title": "Probe PP 2"},
        role=Role.WRITER,
    ),
    **child_routes(
        "personnel",
        "personnel",
        {"name": "Probe Person", "role": "PM"},
        {"role": "Lead"},
        role=Role.WRITER,
    ),
    **child_routes(
        "registrations", "registration", {"kind": "sam", "identifier": "P"}, {"holder": "x"}
    ),
    **child_routes("vehicles", "vehicle", {"vehicle": "GSA MAS", "number": "P"}, {"number": "Q"}),
    **child_routes(
        "insurance", "insurance", {"kind": "cyber", "carrier": "Probe"}, {"carrier": "Probe 2"}
    ),
    **child_routes(
        "boilerplate",
        "boilerplate_block",
        {"kind": "company_overview", "title": "Probe", "body": "<p>probe</p>"},
        {"title": "Probe 2"},
    ),
    # B posting A's file id must fail (not visible); A's owner succeeds with its own file
    **child_routes(
        "files",
        "profile_file",
        lambda ctx: {"file_id": ctx.a.ids["file"], "kind": "brochure"},
        {"title": "Probe"},
    ),
    **child_routes(
        "rate-card",
        "rate_card_entry",
        {"labor_category": "Probe", "unit": "hour", "rate_amount": "1", "rate_currency": "USD"},
        {"rate_amount": "2"},
    ),
    **child_routes(
        "certifications",
        "certification",
        {"kind": "8a", "cert_number": "PROBE-1"},
        {"cert_number": "PROBE-2"},
    ),
    # --- opportunities (M2-15): global public notices, readable by every tenant role; the
    # harness only checks that B's 200 carries none of A's identifiers.
    ("GET", "/api/v1/opportunities"): lambda ctx: RouteCall(params={"q": "isolation", "page": 1}),
    ("GET", "/api/v1/opportunities/{opportunity_id}"): lambda ctx: RouteCall(
        path_params={"opportunity_id": ctx.shared["opportunity"]}
    ),
    # --- integrations (M4-11): tenant owners only; B probes with its own body because the
    # row is addressed by (tenant, kind), so a 200 must still never show A's connection
    ("GET", "/api/v1/integrations"): lambda ctx: RouteCall(),
    ("GET", "/api/v1/integrations/{kind}"): lambda ctx: RouteCall(
        path_params={"kind": "slack"}, owner_expect=frozenset({200, 404})
    ),
    ("PUT", "/api/v1/integrations/{kind}"): lambda ctx: RouteCall(
        path_params={"kind": "slack"},
        json={"enabled": True, "config": {"channel": "#probe"}},
    ),
    # --- pursuits (M5-02): tenant-scoped; B posting A's profile id gets 404 (RLS hides it)
    ("POST", "/api/v1/pursuits"): lambda ctx: RouteCall(
        json={"profile_id": ctx.a.ids["profile"], "opportunity_id": ctx.shared["opportunity"]}
    ),
    ("GET", "/api/v1/pursuits/{pursuit_id}"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}
    ),
    ("PATCH", "/api/v1/pursuits/{pursuit_id}"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}, json={"stage": "qualifying"}
    ),
    # M5-06 Gate 1: only an approver of A's profile may decide, and only inside A
    ("POST", "/api/v1/pursuits/{pursuit_id}/decision"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]},
        json={"decision": "no_bid", "note": "isolation probe"},
    ),
    ("GET", "/api/v1/pursuits/{pursuit_id}/matrix"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}
    ),
    ("GET", "/api/v1/pursuits/{pursuit_id}/packet"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}
    ),
    ("POST", "/api/v1/pursuits/{pursuit_id}/approve-package"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}, json={"note": "probe"}
    ),
    # --- pursuit workspace (M5-16): drafts, approvals and comments
    ("GET", "/api/v1/pursuits/{pursuit_id}/drafts"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}
    ),
    ("GET", "/api/v1/pursuits/{pursuit_id}/drafts/{section_id}"): lambda ctx: RouteCall(
        path_params={
            "pursuit_id": ctx.a.ids["pursuit"],
            "section_id": ctx.a.ids["draft_section"],
        }
    ),
    ("PUT", "/api/v1/pursuits/{pursuit_id}/drafts/{section_id}"): lambda ctx: RouteCall(
        path_params={
            "pursuit_id": ctx.a.ids["pursuit"],
            "section_id": ctx.a.ids["draft_section"],
        },
        json={"body_html": "<p>isolation probe</p>", "base_version": 1},
    ),
    ("POST", "/api/v1/pursuits/{pursuit_id}/drafts/{section_id}/approve"): lambda ctx: RouteCall(
        path_params={
            "pursuit_id": ctx.a.ids["pursuit"],
            "section_id": ctx.a.ids["draft_section"],
        }
    ),
    ("GET", "/api/v1/pursuits/{pursuit_id}/comments"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]}
    ),
    ("POST", "/api/v1/pursuits/{pursuit_id}/comments"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]},
        json={
            "target_type": "draft",
            "target_id": ctx.a.ids["draft"],
            "body": "isolation probe",
        },
        owner_expect=frozenset({201}),
    ),
    ("POST", "/api/v1/pursuits/{pursuit_id}/comments/{comment_id}/resolve"): lambda ctx: RouteCall(
        path_params={
            "pursuit_id": ctx.a.ids["pursuit"],
            "comment_id": ctx.a.ids["comment"],
        }
    ),
    # inline so no broker is needed; B's call must 404 before any run row is created
    ("POST", "/api/v1/pursuits/{pursuit_id}/agents/run"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]},
        json={"step": "pricing", "inline": True},
        owner_expect=frozenset({202}),
    ),
    ("POST", "/api/v1/pursuits/{pursuit_id}/agents/approve-budget"): lambda ctx: RouteCall(
        path_params={"pursuit_id": ctx.a.ids["pursuit"]},
        json={"additional_usd": "5", "reason": "isolation probe"},
    ),
    # --- billing (M7-04): GET /billing must never echo A's customer / subscription /
    # invoice identifiers. The harness installs network-free providers (conftest), so
    # checkout answers 201 and its body is leak-checked too.
    ("GET", "/api/v1/billing"): lambda ctx: RouteCall(role=Role.VIEWER),
    ("POST", "/api/v1/billing/checkout"): lambda ctx: RouteCall(
        json={
            "plan": "pro",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/no",
        }
    ),
    # --- privacy (M7-07): consents and data requests are per user, so B sees none of A's;
    # the tenant jobs are destructive, so the probe is the 409 A gets after its own delete.
    ("GET", "/api/v1/me/consents"): lambda ctx: RouteCall(role=Role.VIEWER),
    ("POST", "/api/v1/me/consents"): lambda ctx: RouteCall(
        json={"kind": "dpdp", "version": "probe"}, role=Role.VIEWER
    ),
    ("GET", "/api/v1/me/data-requests"): lambda ctx: RouteCall(role=Role.VIEWER),
    ("POST", "/api/v1/me/data-requests"): lambda ctx: RouteCall(
        json={"kind": "access"}, role=Role.VIEWER
    ),
    ("POST", "/api/v1/tenant/export"): lambda ctx: RouteCall(owner_expect=frozenset({202})),
    ("POST", "/api/v1/tenant/delete"): lambda ctx: RouteCall(owner_expect=frozenset({202})),
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
        proof = {
            "past_performance": PastPerformance(
                tenant_id=ta.id,
                profile_id=profile.id,
                title="Alpha PP",
                customer="Alpha Agency",
                role=PerformanceRole.PRIME,
                scope="alpha scope",
            ),
            "personnel": Personnel(
                tenant_id=ta.id, profile_id=profile.id, name="Alpha Person", role="PM"
            ),
            "registration": Registration(
                tenant_id=ta.id,
                profile_id=profile.id,
                kind=RegistrationKind.SAM,
                identifier="ALPHA-SAM",
            ),
            "vehicle": Vehicle(
                tenant_id=ta.id, profile_id=profile.id, vehicle="GSA MAS", number="ALPHA-MAS-1"
            ),
            "insurance": Insurance(
                tenant_id=ta.id,
                profile_id=profile.id,
                kind=InsuranceKind.CYBER,
                carrier="Alpha Insurer",
            ),
            "boilerplate_block": BoilerplateBlock(
                tenant_id=ta.id,
                profile_id=profile.id,
                kind=BoilerplateKind.COMPANY_OVERVIEW,
                title="Alpha Overview",
                body="<p>alpha body</p>",
            ),
            "profile_file": ProfileFile(
                tenant_id=ta.id,
                profile_id=profile.id,
                file_id=file.id,
                kind=ProfileFileKind.CAPABILITY_STATEMENT,
                title="Alpha Capability",
            ),
            "rate_card_entry": RateCardEntry(
                tenant_id=ta.id,
                profile_id=profile.id,
                labor_category="Alpha Architect",
                unit=RateUnit.HOUR,
                rate_amount=150,
                rate_currency="USD",
            ),
        }
        notification = Notification(
            tenant_id=ta.id,
            user_id=ua.id,
            event_type="high_fit_match",
            version=1,
            idempotency_key=f"{ua.id}:high_fit_match:{uuid.uuid4()}:1",
            payload={"title": "Alpha secret notice"},
        )
        push = PushSubscription(
            tenant_id=ta.id,
            user_id=ua.id,
            endpoint=f"https://fcm.googleapis.com/fcm/send/alpha-{uuid.uuid4().hex[:8]}",
            p256dh="alpha-p256dh",
            auth="alpha-auth",
        )
        integration = Integration(
            tenant_id=ta.id,
            kind="slack",
            enabled=True,
            config={"channel": "#alpha-bids"},
            secret_ref="env:ALPHA_SLACK_HOOK",
        )
        consent = Consent(
            tenant_id=ta.id, user_id=ua.id, kind="dpdp", version="alpha-consent-v1", ip="10.0.0.1"
        )
        data_request = DataRequest(
            tenant_id=ta.id,
            user_id=ua.id,
            kind="correction",
            details={"note": "alpha secret request note"},
            sla_due_at=datetime.now(UTC) + timedelta(days=30),
        )
        billing_customer = BillingCustomer(
            tenant_id=ta.id,
            provider="stripe",
            customer_id="cus_ALPHASECRET",
            subscription_id="sub_ALPHASECRET",
            status="active",
            gst_details={"gstin": "29AABCU9603R1ZM"},
        )
        billing_event = BillingEventRecord(
            tenant_id=ta.id,
            provider="stripe",
            event_id="evt_ALPHASECRET",
            kind="invoice_paid",
            amount=9900,
            currency="USD",
            payload={"_external_ids": {"invoice_number": "BR-ALPHA-0001"}},
        )
        prefs = UserNotificationPrefs(
            tenant_id=ta.id,
            user_id=ua.id,
            channels_by_event={"digest": ["slack"]},
            tz="Asia/Kolkata",
        )
        opportunity = Opportunity(
            source_id="sam_opps",
            external_id=f"iso-{uuid.uuid4().hex[:8]}",
            region=Region.US,
            country="US",
            currency="USD",
            notice_type=NoticeType.RFP,
            title="Isolation probe notice",
        )
        session.add_all(
            [
                certification,
                code,
                keyword,
                service_line,
                partner,
                prefs,
                integration,
                notification,
                push,
                billing_customer,
                billing_event,
                consent,
                data_request,
                *proof.values(),
            ]
        )
        session.add(opportunity)
        await session.flush()
        pursuit = Pursuit(
            tenant_id=ta.id,
            profile_id=profile.id,
            opportunity_id=opportunity.id,
            created_by=ua.id,
            owner_user_id=ua.id,
        )
        session.add(pursuit)
        await session.flush()
        draft = Draft(
            tenant_id=ta.id,
            pursuit_id=pursuit.id,
            section_id="technical-approach",
            title="Alpha technical approach",
            volume="Volume I - Technical",
        )
        session.add(draft)
        await session.flush()
        draft_version = DraftVersion(
            tenant_id=ta.id,
            draft_id=draft.id,
            version=1,
            body_html="<p>alpha draft body</p>",
            body_text="alpha draft body",
            author="agent",
        )
        session.add(draft_version)
        await session.flush()
        draft.current_version_id = draft_version.id
        comment = Comment(
            tenant_id=ta.id,
            pursuit_id=pursuit.id,
            target_type="draft",
            target_id=draft.id,
            body="alpha secret comment",
            author_user_id=ua.id,
        )
        task = Task(
            tenant_id=ta.id,
            pursuit_id=pursuit.id,
            title="Alpha secret task",
            ref={"kind": "needs_input", "section_id": "technical-approach"},
        )
        # a red-team report so Gate 2 (approve-package) has something to approve
        red_team = PursuitArtifact(
            tenant_id=ta.id,
            pursuit_id=pursuit.id,
            kind="red_team",
            version=1,
            data={"report": {"sections": [], "overall_score": 71, "missing_requirements": []}},
        )
        session.add_all([comment, task, red_team])
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
                "integration": str(integration.id),
                "notification": str(notification.id),
                "push_subscription": str(push.id),
                "push_subscription_endpoint": push.endpoint,
                "service_line": str(service_line.id),
                "service_line_name": "Alpha Cloud Line",
                "teaming_partner": str(partner.id),
                "teaming_partner_name": "Alpha Partner Corp",
                "teaming_partner_uei": "PARTNER12345",
                "teaming_partner_pan": "ABCDE1234F",
                **{key: str(row.id) for key, row in proof.items()},
                "past_performance_title": "Alpha PP",
                "personnel_name": "Alpha Person",
                "registration_identifier": "ALPHA-SAM",
                "boilerplate_title": "Alpha Overview",
                "rate_card_category": "Alpha Architect",
                "notification_prefs": str(prefs.id),
                "pursuit": str(pursuit.id),
                "draft": str(draft.id),
                "draft_section": draft.section_id,
                "draft_version": str(draft_version.id),
                "draft_body": "alpha draft body",
                "comment": str(comment.id),
                "comment_body": "alpha secret comment",
                "task": str(task.id),
                "billing_customer": str(billing_customer.id),
                "billing_customer_id": "cus_ALPHASECRET",
                "billing_subscription_id": "sub_ALPHASECRET",
                "billing_event": str(billing_event.id),
                "billing_invoice_number": "BR-ALPHA-0001",
                "consent": str(consent.id),
                "consent_version": "alpha-consent-v1",
                "data_request": str(data_request.id),
                "data_request_note": "alpha secret request note",
            },
        )
        b = TenantCtx(
            id=tb.id,
            owner_id=ub.id,
            owner_email=ub.email,
            ids={"tenant": str(tb.id), "owner_user": str(ub.id), "membership": str(mb.id)},
        )
        shared = {"opportunity": str(opportunity.id)}
    return IsolationContext(a=a, b=b, shared=shared)
