"""M4-11: Slack Block Kit message, the v0 signature check and the callback parser."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
import respx
from app.core.config import Settings
from app.models import Notification
from app.notify.core import Recipient
from app.notify.slack import (
    ACTION_PREFIX,
    SlackChannel,
    SlackSettings,
    SlackSignatureError,
    build_blocks,
    parse_block_actions,
    sign_request,
    verify_signature,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
HOOK = "https://hooks.slack.com/services/T000/B000/xxxx"
SECRET = "8f742231b10e8888abcd99yyyzzz85a5"

ACTIONS = {
    "pursue": "https://api.example/api/v1/notifications/actions/tok-pursue",
    "watch": "https://api.example/api/v1/notifications/actions/tok-watch",
    "pass": "https://api.example/api/v1/notifications/actions/tok-pass",
    "assign": "https://api.example/api/v1/notifications/actions/tok-assign",
}


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _notification(**overrides: Any) -> Notification:
    values: dict[str, Any] = {
        "id": uuid.UUID("33333333-3333-4333-8333-333333333333"),
        "tenant_id": TENANT,
        "user_id": USER,
        "event_type": "high_fit_match",
        "version": 2,
        "idempotency_key": "k",
        "payload": {
            "title": "Cloud migration services",
            "buyer": "Department of Energy",
            "value_amount": "1200000",
            "value_currency": "USD",
            "response_due_at": DUE.isoformat(),
            "buyer_tz": "America/New_York",
            "score": 82.5,
            "band": "high",
            "deep_link": "https://app.example/app/opportunities/abc",
            "actions": dict(ACTIONS),
            "rationale": {"fit_summary": ["NAICS exact", "Two DOE awards", "Remote ok", "extra"]},
        },
    }
    values.update(overrides)
    return Notification(**values)


def _blocks_of(body: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [b for b in body["blocks"] if b["type"] == kind]


# --- Block Kit ---------------------------------------------------------------------------------


def test_block_kit_message_has_the_three_buttons_and_the_link() -> None:
    body = build_blocks(_notification(), now=NOW)
    assert body["text"] == "High fit 82.5: Cloud migration services"
    assert body["blocks"][0]["type"] == "header"
    serialized = json.dumps(body)
    assert "Cloud migration services" in serialized
    assert "Department of Energy" in serialized
    assert "$1,200,000" in serialized
    assert "Oct 14, 2:00 PM EDT" in serialized and "3d 6h" in serialized
    assert "<https://app.example/app/opportunities/abc|Cloud migration services>" in serialized

    actions = _blocks_of(body, "actions")[0]
    assert actions["block_id"] == str(_notification().id)
    elements = actions["elements"]
    assert [e["action_id"] for e in elements] == [
        f"{ACTION_PREFIX}pursue",
        f"{ACTION_PREFIX}pass",
        f"{ACTION_PREFIX}assign",
        f"{ACTION_PREFIX}open",
    ]
    assert [e["text"]["text"] for e in elements[:3]] == ["Pursue", "Pass", "Assign"]
    # each button carries the signed action token, not a raw id
    assert [e["value"] for e in elements[:3]] == ["tok-pursue", "tok-pass", "tok-assign"]
    assert elements[0]["style"] == "primary" and elements[1]["style"] == "danger"
    assert elements[3]["url"] == "https://app.example/app/opportunities/abc"
    assert "value" not in elements[3]


def test_block_kit_caps_the_rationale_at_three_bullets() -> None:
    body = build_blocks(_notification(), now=NOW)
    bullets = [
        b for b in _blocks_of(body, "section") if "•" in (b.get("text") or {}).get("text", "")
    ]
    assert bullets[0]["text"]["text"].count("•") == 3
    assert "extra" not in bullets[0]["text"]["text"]


def test_block_kit_degrades_without_payload_details() -> None:
    body = build_blocks(
        _notification(event_type="adapter_failing", payload={"source_id": "cppp"}), now=NOW
    )
    assert body["text"] == "Adapter Failing: Adapter Failing"
    assert _blocks_of(body, "actions") == []  # no signed actions -> no buttons
    assert _blocks_of(body, "context")[0]["elements"][0]["text"].endswith("adapter failing")


def test_block_kit_skips_an_unparseable_due_date() -> None:
    body = build_blocks(
        _notification(payload={"title": "T", "response_due_at": "soon", "actions": {}}), now=NOW
    )
    assert "Due" not in json.dumps(body)


# --- outbound channel ---------------------------------------------------------------------------


@respx.mock
async def test_channel_posts_to_the_tenant_webhook() -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))

    async def resolver(tenant_id: uuid.UUID) -> SlackSettings:
        assert tenant_id == TENANT
        return SlackSettings(HOOK, SECRET, {"channel": "#bids"}, True)

    async with httpx.AsyncClient() as client:
        channel = SlackChannel(_settings(), resolver=resolver, client=client, now=lambda: NOW)
        result = await channel.send(None, _notification(), Recipient(USER, ("slack",)))
    assert result.ok
    body = json.loads(route.calls.last.request.content)
    assert body["channel"] == "#bids"
    assert body["blocks"][0]["type"] == "header"


@respx.mock
async def test_channel_reports_webhook_failures() -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(404, text="no_service"))
    async with httpx.AsyncClient() as client:
        channel = SlackChannel(_settings(), webhook_url=HOOK, client=client)
        failed = await channel.send(None, _notification(), Recipient(USER, ("slack",)))
    assert not failed.ok and failed.error is not None and "HTTP 404" in failed.error

    respx.post(HOOK).mock(side_effect=httpx.ConnectTimeout("slow"))
    async with httpx.AsyncClient() as client:
        channel = SlackChannel(_settings(), webhook_url=HOOK, client=client)
        down = await channel.send(None, _notification(), Recipient(USER, ("slack",)))
    assert not down.ok and down.error is not None and "ConnectTimeout" in down.error


async def test_channel_skips_when_the_tenant_has_no_slack() -> None:
    channel = SlackChannel(_settings())
    result = await channel.send(None, _notification(), Recipient(USER, ("slack",)))
    assert result.skipped and not result.ok

    async def disabled(tenant_id: uuid.UUID) -> SlackSettings:
        return SlackSettings(HOOK, SECRET, {}, enabled=False)

    off = await SlackChannel(_settings(), resolver=disabled).send(
        None, _notification(), Recipient(USER, ("slack",))
    )
    assert off.skipped


@respx.mock
async def test_channel_owns_its_client_when_none_is_injected() -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
    channel = SlackChannel(_settings(), webhook_url=HOOK)
    assert (await channel.send(None, _notification(), Recipient(USER, ("slack",)))).ok


# --- signature ----------------------------------------------------------------------------------


def _signed(body: bytes, *, at: datetime = NOW, secret: str = SECRET) -> tuple[str, str]:
    ts = str(int(at.timestamp()))
    return sign_request(secret, ts, body), ts


def test_signature_matches_slacks_v0_recipe() -> None:
    # the worked example from Slack's own documentation
    body = b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J"
    signature = sign_request("8f742231b10e8888abcd99yyyzzz85a5", "1531420618", body)
    assert signature.startswith("v0=")
    assert signature == sign_request("8f742231b10e8888abcd99yyyzzz85a5", "1531420618", body)


def test_valid_fresh_signature_passes() -> None:
    body = b"payload=%7B%7D"
    signature, ts = _signed(body)
    verify_signature(SECRET, signature=signature, timestamp=ts, body=body, now=NOW)
    # still inside the 5-minute window
    verify_signature(
        SECRET, signature=signature, timestamp=ts, body=body, now=NOW + timedelta(seconds=299)
    )


@pytest.mark.parametrize(
    ("secret", "mangle", "at", "reason"),
    [
        (None, None, NOW, "not configured"),
        (SECRET, "signature", NOW, "mismatch"),
        (SECRET, "body", NOW, "mismatch"),
        (SECRET, "timestamp", NOW, "mismatch"),
        (SECRET, None, NOW + timedelta(seconds=301), "replay window"),
        (SECRET, None, NOW - timedelta(seconds=301), "replay window"),
        (SECRET, "missing", NOW, "missing slack signature"),
        (SECRET, "not_a_number", NOW, "malformed"),
        ("other-secret", None, NOW, "mismatch"),
    ],
)
def test_signature_failures(
    secret: str | None, mangle: str | None, at: datetime, reason: str
) -> None:
    body = b"payload=%7B%7D"
    signature, ts = _signed(body)
    if mangle == "signature":
        signature = signature[:-1] + ("0" if signature[-1] != "0" else "1")
    if mangle == "body":
        body = body + b"&extra=1"
    if mangle == "timestamp":
        ts = str(int(ts) + 1)
    if mangle == "missing":
        signature = ""
    if mangle == "not_a_number":
        ts = "not-a-number"
    with pytest.raises(SlackSignatureError, match=reason):
        verify_signature(secret, signature=signature, timestamp=ts, body=body, now=at)


# --- callback parsing -----------------------------------------------------------------------------


def _callback(action: str = "pursue", token: str = "tok-pursue", **overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "type": "block_actions",
        "user": {"id": "U42", "username": "ada"},
        "team": {"id": "T1"},
        "response_url": "https://hooks.slack.com/actions/T1/1/2",
        "actions": [
            {"action_id": "some_other_app_button", "value": "ignored"},
            {"action_id": f"{ACTION_PREFIX}{action}", "value": token, "type": "button"},
        ],
    }
    payload.update(overrides)
    return urlencode({"payload": json.dumps(payload)}).encode()


def test_parse_block_actions_finds_the_bidradar_button() -> None:
    parsed = parse_block_actions(_callback("pass", "tok-pass"))
    assert parsed.action == "pass"
    assert parsed.token == "tok-pass"
    assert parsed.user == "ada" and parsed.team_id == "T1"
    assert parsed.response_url is not None


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"", "missing"),
        (urlencode({"payload": "not json"}).encode(), "not JSON"),
        (urlencode({"payload": json.dumps({"type": "view_submission"})}).encode(), "unsupported"),
        (
            urlencode(
                {"payload": json.dumps({"type": "block_actions", "actions": [{"action_id": "x"}]})}
            ).encode(),
            "no BidRadar action",
        ),
        (_callback("watch", "tok-watch"), "no BidRadar action"),
    ],
)
def test_parse_block_actions_rejects_everything_else(body: bytes, reason: str) -> None:
    with pytest.raises(SlackSignatureError, match=reason):
        parse_block_actions(body)
