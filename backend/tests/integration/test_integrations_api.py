"""M4-11: the integrations API and the signed Slack interactive callback."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from app.core.config import Settings
from app.core.db import Database
from app.core.roles import Role
from app.models import AuditLog, Integration, Notification
from app.notify.actions import sign_action_token
from app.notify.slack import (
    ACTION_PREFIX,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    load_slack_settings,
    sign_request,
)
from app.services.secrets import (
    SecretUnavailableError,
    encrypt_secret,
    resolve_secret,
    resolve_secret_mapping,
    secret_scheme,
)
from sqlalchemy import select

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner

HOOK = "https://hooks.slack.com/services/T000/B000/xxxx"
SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"


async def _tenant(database: Database) -> tuple[uuid.UUID, uuid.UUID]:
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session)
        return tenant.id, user.id


async def _notification(database: Database, tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    async with database.session(tenant_id) as session:
        row = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type="high_fit_match",
            version=2,
            idempotency_key=f"{user_id}:high_fit_match:{uuid.uuid4()}:2",
            payload={"title": "Cloud migration services", "actions_taken": []},
        )
        session.add(row)
        await session.flush()
        return row.id


async def _connect_slack(
    api_client: httpx.AsyncClient, tenant_id: uuid.UUID, user_id: uuid.UUID, **body: Any
) -> httpx.Response:
    payload: dict[str, Any] = {
        "enabled": True,
        "config": {"channel": "#bids"},
        "webhook_url": HOOK,
        "signing_secret": SIGNING,
    }
    payload.update(body)
    return await api_client.put(
        "/api/v1/integrations/slack",
        json=payload,
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER),
    )


# --- secret references ---------------------------------------------------------------------------


def test_secret_reference_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_HOOK_ACME", HOOK)
    assert resolve_secret("env:SLACK_HOOK_ACME") == HOOK
    assert resolve_secret_mapping("env:SLACK_HOOK_ACME") == {"webhook_url": HOOK}
    assert resolve_secret(None) is None and resolve_secret_mapping(None) == {}

    ref = encrypt_secret({"webhook_url": HOOK, "signing_secret": SIGNING})
    assert ref.startswith("enc:") and HOOK not in ref
    assert resolve_secret_mapping(ref) == {"webhook_url": HOOK, "signing_secret": SIGNING}
    assert secret_scheme(ref) == "enc" and secret_scheme("env:X") == "env"
    assert secret_scheme("sm://projects/p/secrets/s/versions/1") == "sm"
    assert secret_scheme("https://hooks.slack.com/x") is None

    monkeypatch.setenv("SECRET_SLACK_ACME", HOOK)
    assert resolve_secret("sm://projects/p/secrets/slack-acme/versions/latest") == HOOK
    for bad, reason in [
        ("env:NOT_SET_ANYWHERE", "is not set"),
        ("sm://projects/p/secrets/absent/versions/1", "not mounted"),
        ("enc:v1:AAAA:BBBB", "cannot be decrypted"),
        ("https://hooks.slack.com/x", "unknown secret reference"),
    ]:
        with pytest.raises(SecretUnavailableError, match=reason):
            resolve_secret(bad)
    with pytest.raises(SecretUnavailableError, match="not valid JSON"):
        resolve_secret_mapping(encrypt_secret("{nope"))
    with pytest.raises(SecretUnavailableError, match="must be a JSON object"):
        resolve_secret_mapping(encrypt_secret("[1, 2]"))


# --- integrations API -----------------------------------------------------------------------------


async def test_owner_connects_slack_and_secrets_are_never_read_back(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id = await _tenant(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER)
    assert (await api_client.get("/api/v1/integrations/slack", headers=headers)).status_code == 404

    created = await _connect_slack(api_client, tenant_id, user_id)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body == {
        "kind": "slack",
        "enabled": True,
        "config": {"channel": "#bids"},
        "secret_scheme": "enc",
        "secret_set": True,
    }
    listed = (await api_client.get("/api/v1/integrations", headers=headers)).json()
    assert [row["kind"] for row in listed] == ["slack"]
    assert HOOK not in json.dumps(listed) and SIGNING not in json.dumps(listed)

    async with database.session(tenant_id) as session:
        row = (await session.execute(select(Integration))).scalars().one()
        assert HOOK not in (row.secret_ref or "")  # encrypted at rest
        resolved = await load_slack_settings(session, tenant_id)
        assert resolved is not None
        assert resolved.webhook_url == HOOK and resolved.signing_secret == SIGNING
        assert resolved.config == {"channel": "#bids"}

    # updating only the config keeps the stored secret
    again = await api_client.put(
        "/api/v1/integrations/slack",
        json={"config": {"channel": "#ops"}},
        headers=headers,
    )
    assert again.json()["config"] == {"channel": "#ops"} and again.json()["secret_set"] is True
    async with database.session(tenant_id) as session:
        resolved = await load_slack_settings(session, tenant_id)
        assert resolved is not None and resolved.webhook_url == HOOK

    audits = [r.action for r in await _audits(database, tenant_id)]
    assert audits.count("integration.update") == 2


async def _audits(database: Database, tenant_id: uuid.UUID) -> list[AuditLog]:
    async with database.owner_session() as session:
        return list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(AuditLog.at)
                )
            )
            .scalars()
            .all()
        )


async def test_integration_validation_and_roles(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id, user_id = await _tenant(database)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.TENANT_OWNER)
    # a secret hidden inside config would be readable by everyone
    leaked = await api_client.put(
        "/api/v1/integrations/slack",
        json={"config": {"webhook_url": HOOK}},
        headers=headers,
    )
    assert leaked.status_code == 422 and "config" in leaked.text

    both = await _connect_slack(api_client, tenant_id, user_id, secret_ref="env:X")
    assert both.status_code == 422 and "not both" in both.text

    bad_ref = await api_client.put(
        "/api/v1/integrations/teams",
        json={"secret_ref": HOOK},
        headers=headers,
    )
    assert bad_ref.status_code == 422 and "env:" in bad_ref.text

    ok_ref = await api_client.put(
        "/api/v1/integrations/teams",
        json={"secret_ref": "env:TEAMS_HOOK_ACME"},
        headers=headers,
    )
    assert ok_ref.json()["secret_scheme"] == "env"

    unknown = await api_client.put(
        "/api/v1/integrations/pigeon", json={"enabled": True}, headers=headers
    )
    assert unknown.status_code == 422

    writer = auth_headers(user_id=user_id, tenant_id=tenant_id, role=Role.WRITER)
    assert (await api_client.get("/api/v1/integrations", headers=writer)).status_code == 403
    assert (
        await api_client.put("/api/v1/integrations/slack", json={"enabled": False}, headers=writer)
    ).status_code == 403


async def test_integrations_are_tenant_isolated(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tenant_a, user_a = await _tenant(database)
    tenant_b, user_b = await _tenant(database)
    await _connect_slack(api_client, tenant_a, user_a)
    b_headers = auth_headers(user_id=user_b, tenant_id=tenant_b, role=Role.TENANT_OWNER)
    assert (await api_client.get("/api/v1/integrations", headers=b_headers)).json() == []
    assert (
        await api_client.get("/api/v1/integrations/slack", headers=b_headers)
    ).status_code == 404
    async with database.session(tenant_b) as session:
        assert await load_slack_settings(session, tenant_b) is None


# --- Slack callback -------------------------------------------------------------------------------


def _callback_body(token: str, action: str = "pursue", user: str = "ada") -> bytes:
    payload = {
        "type": "block_actions",
        "user": {"id": "U42", "username": user},
        "team": {"id": "T1"},
        "actions": [{"action_id": f"{ACTION_PREFIX}{action}", "value": token, "type": "button"}],
    }
    return urlencode({"payload": json.dumps(payload)}).encode()


def _slack_headers(
    body: bytes, *, secret: str = SIGNING, at: datetime | None = None
) -> dict[str, str]:
    ts = str(int((at or datetime.now(UTC)).timestamp()))
    return {
        TIMESTAMP_HEADER: ts,
        SIGNATURE_HEADER: sign_request(secret, ts, body),
        "content-type": "application/x-www-form-urlencoded",
    }


async def test_slack_button_records_the_action(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id = await _tenant(database)
    await _connect_slack(api_client, tenant_id, user_id)
    notification_id = await _notification(database, tenant_id, user_id)
    token = sign_action_token(
        settings,
        action="pursue",
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification_id,
    )
    body = _callback_body(token)
    response = await api_client.post(
        "/api/v1/integrations/slack/actions", content=body, headers=_slack_headers(body)
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "action": "pursue",
        "notification_id": str(notification_id),
        "recorded": True,
        "text": "Recorded *pursue* by ada",
    }
    async with database.session(tenant_id) as session:
        row = await session.get(Notification, notification_id)
        assert row is not None
        assert [(t["action"], t["source"]) for t in row.payload["actions_taken"]] == [
            ("pursue", "slack")
        ]
    assert "integration.slack.action" in [a.action for a in await _audits(database, tenant_id)]


async def test_slack_assign_records_the_clicking_user(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id = await _tenant(database)
    await _connect_slack(api_client, tenant_id, user_id)
    notification_id = await _notification(database, tenant_id, user_id)
    token = sign_action_token(
        settings,
        action="assign",
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification_id,
    )
    body = _callback_body(token, "assign", user="grace")
    response = await api_client.post(
        "/api/v1/integrations/slack/actions", content=body, headers=_slack_headers(body)
    )
    assert response.status_code == 200, response.text
    async with database.session(tenant_id) as session:
        row = await session.get(Notification, notification_id)
        assert row is not None and row.payload["actions_taken"][0]["assignee"] == "grace"


async def test_slack_callback_rejects_bad_signatures_and_mismatched_actions(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id = await _tenant(database)
    await _connect_slack(api_client, tenant_id, user_id)
    notification_id = await _notification(database, tenant_id, user_id)
    token = sign_action_token(
        settings,
        action="pursue",
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification_id,
    )
    body = _callback_body(token)

    # no signature at all
    unsigned = await api_client.post("/api/v1/integrations/slack/actions", content=body)
    assert unsigned.status_code == 401 and "signature" in unsigned.text

    # signed with the wrong secret
    wrong = await api_client.post(
        "/api/v1/integrations/slack/actions",
        content=body,
        headers=_slack_headers(body, secret="not-the-signing-secret"),
    )
    assert wrong.status_code == 401 and "mismatch" in wrong.text

    # replayed outside the 5-minute window
    stale_headers = _slack_headers(body, at=datetime.now(UTC).replace(microsecond=0))
    stale_headers[TIMESTAMP_HEADER] = str(int(stale_headers[TIMESTAMP_HEADER]) - 600)
    stale_headers[SIGNATURE_HEADER] = sign_request(SIGNING, stale_headers[TIMESTAMP_HEADER], body)
    stale = await api_client.post(
        "/api/v1/integrations/slack/actions", content=body, headers=stale_headers
    )
    assert stale.status_code == 401 and "replay window" in stale.text

    # the body was tampered with after signing
    tampered = await api_client.post(
        "/api/v1/integrations/slack/actions",
        content=_callback_body(token, "pass"),
        headers=_slack_headers(body),
    )
    assert tampered.status_code == 401

    # a button whose action does not match its token
    mismatched = _callback_body(token, "pass")
    mismatch = await api_client.post(
        "/api/v1/integrations/slack/actions",
        content=mismatched,
        headers=_slack_headers(mismatched),
    )
    assert mismatch.status_code == 400 and "does not match" in mismatch.text

    async with database.session(tenant_id) as session:
        row = await session.get(Notification, notification_id)
        assert row is not None and row.payload["actions_taken"] == []


async def test_slack_callback_rejects_bad_tokens_and_payloads(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id = await _tenant(database)
    await _connect_slack(api_client, tenant_id, user_id)

    garbage = b"not-a-form"
    assert (
        await api_client.post(
            "/api/v1/integrations/slack/actions",
            content=garbage,
            headers=_slack_headers(garbage),
        )
    ).status_code == 400

    forged_settings = Settings(_env_file=None, auth_secret="another-secret-0123456789abcdef01")  # type: ignore[call-arg]
    forged = _callback_body(
        sign_action_token(
            forged_settings,
            action="pursue",
            tenant_id=tenant_id,
            user_id=user_id,
            notification_id=uuid.uuid4(),
        )
    )
    assert (
        await api_client.post(
            "/api/v1/integrations/slack/actions", content=forged, headers=_slack_headers(forged)
        )
    ).status_code == 401

    # valid token, but the notification does not exist
    ghost = _callback_body(
        sign_action_token(
            settings,
            action="pursue",
            tenant_id=tenant_id,
            user_id=user_id,
            notification_id=uuid.uuid4(),
        )
    )
    assert (
        await api_client.post(
            "/api/v1/integrations/slack/actions", content=ghost, headers=_slack_headers(ghost)
        )
    ).status_code == 404


async def test_slack_callback_refuses_when_the_tenant_has_no_signing_secret(
    api_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    tenant_id, user_id = await _tenant(database)
    notification_id = await _notification(database, tenant_id, user_id)
    token = sign_action_token(
        settings,
        action="pursue",
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification_id,
    )
    body = _callback_body(token)
    response = await api_client.post(
        "/api/v1/integrations/slack/actions", content=body, headers=_slack_headers(body)
    )
    assert response.status_code == 401 and "not configured" in response.text
