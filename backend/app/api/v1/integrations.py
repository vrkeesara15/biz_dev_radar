"""Tenant integrations (SPEC 7, 10.3).

    GET    /api/v1/integrations                 every connection for the tenant
    GET    /api/v1/integrations/{kind}          one connection (secrets never returned)
    PUT    /api/v1/integrations/{kind}          owners connect / update / disable one
    POST   /api/v1/integrations/slack/actions   Slack's interactive callback

The callback is public: Slack has no bearer token. It authenticates twice — the button's
value is a BidRadar action token (HS256 over AUTH_SECRET, naming the tenant, user and
notification) and the request must carry a valid, fresh `X-Slack-Signature` computed with
that tenant's signing secret.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, SettingsDep, TenantSessionDep, client_ip, require_role
from app.core.db import get_database
from app.core.roles import Role
from app.models import Integration, IntegrationKind
from app.notify.actions import ActionTokenError, verify_action_token
from app.notify.actions import record_action as record_notification_action
from app.notify.slack import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SlackSignatureError,
    load_slack_settings,
    parse_block_actions,
    verify_signature,
)
from app.services.audit import AuditHint, write_audit
from app.services.secrets import encrypt_secret, secret_scheme

router = APIRouter(prefix="/integrations", tags=["integrations"])

OwnerDep = Annotated[CurrentUser, Depends(require_role(Role.TENANT_OWNER))]
SLACK_ACTION_AUDIT = "integration.slack.action"
SECRET_FIELDS = ("webhook_url", "signing_secret", "api_key")


class IntegrationOut(BaseModel):
    kind: IntegrationKind
    enabled: bool
    config: dict[str, Any]
    # never the secret itself: the scheme ("env", "sm", "enc") and whether one is stored
    secret_scheme: str | None
    secret_set: bool


class IntegrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    # paste-in secrets: encrypted at rest under FIELD_ENCRYPTION_KEY and never read back
    webhook_url: Annotated[str | None, Field(max_length=2048)] = None
    signing_secret: Annotated[str | None, Field(max_length=512)] = None
    # or point at a secret the platform already holds ("env:NAME", "sm://projects/...")
    secret_ref: Annotated[str | None, Field(max_length=512)] = None


def _out(row: Integration) -> IntegrationOut:
    return IntegrationOut(
        kind=IntegrationKind(row.kind),
        enabled=row.enabled,
        config=dict(row.config or {}),
        secret_scheme=secret_scheme(row.secret_ref),
        secret_set=bool(row.secret_ref),
    )


def _clean_config(config: dict[str, Any]) -> dict[str, Any]:
    """A secret pasted into `config` would be readable by every member: refuse it."""
    leaked = sorted(set(config) & set(SECRET_FIELDS))
    if leaked:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{leaked} must be sent as its own field, not inside config",
        )
    return config


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(user: OwnerDep, session: TenantSessionDep) -> list[IntegrationOut]:
    rows = (await session.execute(select(Integration).order_by(Integration.kind))).scalars().all()
    return [_out(row) for row in rows]


@router.get("/{kind}", response_model=IntegrationOut)
async def read_integration(
    kind: IntegrationKind, user: OwnerDep, session: TenantSessionDep
) -> IntegrationOut:
    row = (
        await session.execute(select(Integration).where(Integration.kind == kind.value))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{kind.value} is not connected")
    return _out(row)


@router.put("/{kind}", response_model=IntegrationOut)
async def upsert_integration(
    kind: IntegrationKind,
    body: IntegrationIn,
    user: OwnerDep,
    session: TenantSessionDep,
    request: Request,
) -> IntegrationOut:
    """Connect or update one integration. Pasted secrets are encrypted into `secret_ref`;
    a `secret_ref` of "env:..." / "sm://..." points at a platform-held secret instead."""
    sent = body.model_dump(exclude_unset=True)
    pasted = {name: sent[name] for name in SECRET_FIELDS if sent.get(name)}
    if pasted and body.secret_ref:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="send either the secrets themselves or a secret_ref, not both",
        )
    if body.secret_ref and secret_scheme(body.secret_ref) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="secret_ref must start with env:, sm:// or enc:",
        )
    row = (
        await session.execute(select(Integration).where(Integration.kind == kind.value))
    ).scalar_one_or_none()
    if row is None:
        row = Integration(tenant_id=user.tenant_id, kind=kind.value, config={}, enabled=True)
        session.add(row)
    if "enabled" in sent:
        row.enabled = body.enabled
    if "config" in sent:
        row.config = _clean_config(body.config)
    if pasted:
        row.secret_ref = encrypt_secret(pasted)
    elif body.secret_ref:
        row.secret_ref = body.secret_ref
    await session.flush()
    request.state.audit = AuditHint(
        action="integration.update",
        object_type="integration",
        object_id=str(row.id),
        meta={"kind": kind.value, "secret_changed": bool(pasted or body.secret_ref)},
    )
    return _out(row)


# --- Slack interactive callback -----------------------------------------------------------------


class SlackActionOut(BaseModel):
    action: str
    notification_id: uuid.UUID
    recorded: bool
    # Slack replaces the message with this text when the body is returned to the webhook
    text: str


@router.post("/slack/actions", response_model=SlackActionOut)
async def slack_actions(request: Request, settings: SettingsDep) -> SlackActionOut:
    """Record a Pursue / Pass / Assign click made from a Slack Block Kit message."""
    body = await request.body()
    try:
        action = parse_block_actions(body)
    except SlackSignatureError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    try:
        claims = verify_action_token(action.token, settings.auth_secret)
    except ActionTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc
    async with get_database().session(claims.tenant_id) as session:
        slack = await load_slack_settings(session, claims.tenant_id)
        try:
            verify_signature(
                slack.signing_secret if slack else None,
                signature=request.headers.get(SIGNATURE_HEADER),
                timestamp=request.headers.get(TIMESTAMP_HEADER),
                body=body,
            )
        except SlackSignatureError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        if claims.action.value != action.action:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="slack action does not match its token"
            )
        try:
            row = await record_notification_action(
                session,
                claims,
                assignee=action.user if action.action == "assign" else None,
                source="slack",
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="notification not found") from exc
        await write_audit(
            session,
            tenant_id=claims.tenant_id,
            user_id=claims.user_id,
            action=SLACK_ACTION_AUDIT,
            object_type="notification",
            object_id=row.id,
            ip=client_ip(request, settings),
            meta={"action": action.action, "slack_user": action.user, "team": action.team_id},
        )
    return SlackActionOut(
        action=action.action,
        notification_id=claims.notification_id,
        recorded=True,
        text=f"Recorded *{action.action}*" + (f" by {action.user}" if action.user else ""),
    )
