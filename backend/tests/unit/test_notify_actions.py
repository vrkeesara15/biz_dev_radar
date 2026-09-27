"""M4-09: signed one-click action links (Pursue / Watch / Pass / Assign)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.core.config import Settings
from app.notify.actions import (
    ACTION_PATH,
    ActionTokenError,
    NotificationAction,
    action_links,
    deep_link,
    sign_action_token,
    verify_action_token,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None, api_base_url="https://api.example.test/")  # type: ignore[call-arg]
IDS = {
    "tenant_id": uuid.uuid4(),
    "user_id": uuid.uuid4(),
    "notification_id": uuid.uuid4(),
    "opportunity_id": uuid.uuid4(),
}


def test_action_links_cover_the_four_actions_and_verify() -> None:
    links = action_links(SETTINGS, now=NOW, **IDS)
    assert set(links) == {"pursue", "watch", "pass", "assign"}
    for name, url in links.items():
        prefix = "https://api.example.test" + ACTION_PATH.format(token="")
        assert url.startswith(prefix)
        claims = verify_action_token(url[len(prefix) :], SETTINGS.auth_secret, now=NOW)
        assert claims.action == NotificationAction(name)
        assert claims.tenant_id == IDS["tenant_id"] and claims.user_id == IDS["user_id"]
        assert claims.notification_id == IDS["notification_id"]
        assert claims.opportunity_id == IDS["opportunity_id"] and claims.pursuit_id is None


def test_token_expires_after_the_configured_ttl() -> None:
    token = sign_action_token(SETTINGS, action="pursue", now=NOW, **IDS)
    ttl = timedelta(seconds=SETTINGS.notify_action_ttl_seconds)
    assert verify_action_token(token, SETTINGS.auth_secret, now=NOW + ttl - timedelta(seconds=1))
    with pytest.raises(ActionTokenError):
        verify_action_token(token, SETTINGS.auth_secret, now=NOW + ttl)
    short = Settings(_env_file=None, notify_action_ttl_seconds=60)  # type: ignore[call-arg]
    token = sign_action_token(short, action="watch", now=NOW, **IDS)
    with pytest.raises(ActionTokenError):
        verify_action_token(token, short.auth_secret, now=NOW + timedelta(seconds=61))


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        # signed with another secret
        sign_action_token(
            Settings(_env_file=None, auth_secret="other-secret-0123456789abcdef0123456789abc"),  # type: ignore[call-arg]
            action="pursue",
            now=NOW,
            **IDS,
        ),
        # a session JWT is not an action token
        jwt.encode(
            {"sub": str(IDS["user_id"]), "exp": int(NOW.timestamp()) + 3600, "action": "pursue",
             "notification_id": str(IDS["notification_id"]), "kind": "session"},
            SETTINGS.auth_secret,
            algorithm="HS256",
        ),
        # alg=none
        jwt.encode({"kind": "notify_action", "action": "pursue", "exp": 9999999999,
                    "notification_id": str(IDS["notification_id"])}, "", algorithm="none"),
        # unknown action / malformed claims
        jwt.encode({"kind": "notify_action", "action": "delete", "exp": 9999999999,
                    "tenant_id": str(IDS["tenant_id"]), "user_id": str(IDS["user_id"]),
                    "notification_id": str(IDS["notification_id"])},
                   SETTINGS.auth_secret, algorithm="HS256"),
        jwt.encode({"kind": "notify_action", "action": "pursue", "exp": "soon",
                    "notification_id": "x"}, SETTINGS.auth_secret, algorithm="HS256"),
    ],
    ids=[
        "empty", "garbage", "wrong-secret", "session-token", "alg-none", "bad-action", "bad-claims"
    ],
)  # fmt: skip
def test_invalid_tokens_are_rejected(token: str) -> None:
    with pytest.raises(ActionTokenError):
        verify_action_token(token, SETTINGS.auth_secret, now=NOW)
    with pytest.raises(ActionTokenError):
        verify_action_token(token, "", now=NOW)


def test_deep_links() -> None:
    settings = Settings(_env_file=None, app_base_url="https://app.example.test/")  # type: ignore[call-arg]
    opp, pursuit = uuid.uuid4(), uuid.uuid4()
    assert deep_link(settings, opp, None) == f"https://app.example.test/app/opportunities/{opp}"
    assert deep_link(settings, opp, pursuit) == f"https://app.example.test/app/pursuits/{pursuit}"
    assert deep_link(settings, None, None) == "https://app.example.test/app"
