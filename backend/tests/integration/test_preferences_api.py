"""M1-06: user_notification_prefs API, profile scoring/bid weights, approvers, languages."""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.core.config import Region
from app.core.db import Database
from app.core.preferences import DEFAULT_BID_NO_BID_WEIGHTS, DEFAULT_SCORING_WEIGHTS
from app.core.roles import Role
from app.models import UserNotificationPrefs
from sqlalchemy import select, text

from tests.auth import auth_headers
from tests.factories import create_tenant_with_owner


async def _tenant(database: Database, **overrides):  # type: ignore[no-untyped-def]
    async with database.owner_session() as session:
        tenant, user, _ = await create_tenant_with_owner(session, **overrides)
        return tenant.id, user.id


async def _profile(api_client: httpx.AsyncClient, database: Database, region: str = "us"):  # type: ignore[no-untyped-def]
    tid, uid = await _tenant(database, region=Region(region), data_residency=Region(region))
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.post(
        "/api/v1/profiles", json={"region": region, "legal_name": "Prefs Co"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return tid, headers, r.json()["id"]


async def test_notification_prefs_defaults_and_update(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid, role=Role.WRITER)
    r = await api_client.get("/api/v1/me/notification-prefs", headers=headers)
    assert r.status_code == 200, r.text
    prefs = r.json()
    assert prefs["user_id"] == str(uid) and prefs["tenant_id"] == str(tid)
    assert prefs["min_score_instant"] == 70 and prefs["min_score_digest"] == 50
    assert prefs["digest_time"] == "08:00" and prefs["tz"] == "UTC"
    assert prefs["quiet_hours_start"] is None and prefs["quiet_hours_end"] is None
    assert prefs["channels_by_event"]["high_fit_match"] == ["email"]
    assert set(prefs["channels_by_event"]) >= {"high_fit_match", "digest", "amendment"}
    # a second read does not create a second row
    await api_client.get("/api/v1/me/notification-prefs", headers=headers)
    async with database.owner_session() as session:
        rows = (await session.execute(select(UserNotificationPrefs))).scalars().all()
        assert len(rows) == 1 and rows[0].user_id == uid and rows[0].tenant_id == tid
        raw = (
            await session.execute(
                text("SELECT min_score_instant, min_score_digest FROM user_notification_prefs")
            )
        ).one()
    assert tuple(raw) == (70, 50)

    body = {
        "channels_by_event": {"high_fit_match": ["slack", "email"], "digest": ["email"]},
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "tz": "Asia/Kolkata",
        "digest_time": "09:30",
        "min_score_instant": 75,
        "min_score_digest": 55,
    }
    r = await api_client.put("/api/v1/me/notification-prefs", json=body, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    for key, value in body.items():
        if key == "channels_by_event":
            assert out[key]["high_fit_match"] == ["slack", "email"]
            assert out[key]["amendment"] == ["email"]  # untouched events keep the default
        else:
            assert out[key] == value, key
    again = (await api_client.get("/api/v1/me/notification-prefs", headers=headers)).json()
    assert again == out


@pytest.mark.parametrize(
    "body",
    [
        {"min_score_instant": 40, "min_score_digest": 60},
        {"min_score_instant": 101},
        {"min_score_digest": -1},
        {"quiet_hours_start": "22:00"},
        {"quiet_hours_start": "25:00", "quiet_hours_end": "07:00"},
        {"quiet_hours_start": "08:00", "quiet_hours_end": "08:00"},
        {"digest_time": "8am"},
        {"tz": "Mars/Olympus"},
        {"channels_by_event": {"birthday": ["email"]}},
        {"channels_by_event": {"digest": ["pager"]}},
        {"channels_by_event": None},
        {"unknown": 1},
    ],
)
async def test_notification_prefs_validation(
    api_client: httpx.AsyncClient, database: Database, body: dict
) -> None:
    tid, uid = await _tenant(database)
    headers = auth_headers(user_id=uid, tenant_id=tid)
    r = await api_client.put("/api/v1/me/notification-prefs", json=body, headers=headers)
    assert r.status_code == 422, r.text


async def test_notification_prefs_are_per_user_and_tenant(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    tid, uid = await _tenant(database)
    other_tid, _ = await _tenant(database)
    mine = auth_headers(user_id=uid, tenant_id=tid)
    colleague = auth_headers(user_id=uuid.uuid4(), tenant_id=tid, role=Role.VIEWER)
    r = await api_client.put(
        "/api/v1/me/notification-prefs", json={"min_score_instant": 90}, headers=mine
    )
    assert r.status_code == 200
    assert (await api_client.get("/api/v1/me/notification-prefs", headers=colleague)).json()[
        "min_score_instant"
    ] == 70
    # the same user in another tenant has separate prefs
    elsewhere = auth_headers(user_id=uid, tenant_id=other_tid)
    assert (await api_client.get("/api/v1/me/notification-prefs", headers=elsewhere)).json()[
        "min_score_instant"
    ] == 70
    async with database.owner_session() as session:
        rows = (await session.execute(select(UserNotificationPrefs))).scalars().all()
    assert len(rows) == 3
    # the app role cannot see another tenant's rows
    async with database.session(other_tid) as session:
        visible = (await session.execute(select(UserNotificationPrefs))).scalars().all()
    assert [r.tenant_id for r in visible] == [other_tid]
    # quiet hours can be cleared together
    r = await api_client.put(
        "/api/v1/me/notification-prefs",
        json={"quiet_hours_start": "22:00", "quiet_hours_end": "06:00"},
        headers=mine,
    )
    assert r.status_code == 200
    r = await api_client.put(
        "/api/v1/me/notification-prefs",
        json={"quiet_hours_start": None, "quiet_hours_end": None},
        headers=mine,
    )
    assert r.status_code == 200 and r.json()["quiet_hours_start"] is None


async def test_profile_scoring_and_bid_weights(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, headers, pid = await _profile(api_client, database)
    created = (await api_client.get(f"/api/v1/profiles/{pid}", headers=headers)).json()
    assert created["scoring_weights"] == DEFAULT_SCORING_WEIGHTS
    assert created["bid_no_bid_weights"] == DEFAULT_BID_NO_BID_WEIGHTS
    assert created["required_approver_roles"] == ["bid_manager"]
    assert created["output_languages"] == ["en"]
    async with database.owner_session() as session:
        raw = (
            await session.execute(
                text("SELECT scoring_weights FROM company_profiles WHERE id = :id"),
                {"id": uuid.UUID(pid)},
            )
        ).scalar_one()
    assert raw == DEFAULT_SCORING_WEIGHTS  # database default, not application default
    edited = {**DEFAULT_SCORING_WEIGHTS, "code_match": 30, "semantic_similarity": 20}
    r = await api_client.put(
        f"/api/v1/profiles/{pid}", json={"scoring_weights": edited}, headers=headers
    )
    assert r.status_code == 200 and r.json()["scoring_weights"] == edited
    for bad in (
        {**DEFAULT_SCORING_WEIGHTS, "code_match": 30},
        {k: v for k, v in DEFAULT_SCORING_WEIGHTS.items() if k != "geography"},
        {**DEFAULT_SCORING_WEIGHTS, "extra": 0},
        {**DEFAULT_SCORING_WEIGHTS, "code_match": -5, "geography": 35},
        None,
    ):
        r = await api_client.put(
            f"/api/v1/profiles/{pid}", json={"scoring_weights": bad}, headers=headers
        )
        assert r.status_code == 422, bad
    assert (
        "sum to 100"
        in (
            await api_client.put(
                f"/api/v1/profiles/{pid}",
                json={"scoring_weights": {**DEFAULT_SCORING_WEIGHTS, "code_match": 30}},
                headers=headers,
            )
        ).text
    )
    bid = {**DEFAULT_BID_NO_BID_WEIGHTS, "fit": 30, "value": 5}
    r = await api_client.put(
        f"/api/v1/profiles/{pid}", json={"bid_no_bid_weights": bid}, headers=headers
    )
    assert r.status_code == 200 and r.json()["bid_no_bid_weights"] == bid
    assert (
        await api_client.put(
            f"/api/v1/profiles/{pid}", json={"bid_no_bid_weights": {"fit": 100}}, headers=headers
        )
    ).status_code == 422


async def test_required_approvers_and_output_languages(
    api_client: httpx.AsyncClient, database: Database
) -> None:
    _, us, us_pid = await _profile(api_client, database)
    _, india, in_pid = await _profile(api_client, database, region="in")
    r = await api_client.put(
        f"/api/v1/profiles/{us_pid}",
        json={"required_approver_roles": ["tenant_owner", "bid_manager", "bid_manager"]},
        headers=us,
    )
    assert r.status_code == 200 and r.json()["required_approver_roles"] == [
        "tenant_owner",
        "bid_manager",
    ]
    for bad in (["platform_admin"], ["viewer"], ["ceo"], None):
        assert (
            await api_client.put(
                f"/api/v1/profiles/{us_pid}", json={"required_approver_roles": bad}, headers=us
            )
        ).status_code == 422, bad
    assert (
        await api_client.put(
            f"/api/v1/profiles/{us_pid}", json={"required_approver_roles": []}, headers=us
        )
    ).status_code == 200

    r = await api_client.put(
        f"/api/v1/profiles/{us_pid}", json={"output_languages": ["en", "hi"]}, headers=us
    )
    assert r.status_code == 422, r.text
    assert (
        r.json()["detail"]["error"] == "region_mismatch"
        and "'hi'" in r.json()["detail"]["fields"][0]
    )
    r = await api_client.put(
        f"/api/v1/profiles/{in_pid}", json={"output_languages": ["EN", "hi"]}, headers=india
    )
    assert r.status_code == 200 and r.json()["output_languages"] == ["en", "hi"]
    assert (
        await api_client.put(
            f"/api/v1/profiles/{in_pid}", json={"output_languages": ["hi"]}, headers=india
        )
    ).status_code == 200
    assert (
        await api_client.put(
            f"/api/v1/profiles/{in_pid}", json={"output_languages": ["ta"]}, headers=india
        )
    ).status_code == 422
    assert (
        await api_client.put(
            f"/api/v1/profiles/{in_pid}", json={"output_languages": []}, headers=india
        )
    ).status_code == 422
    assert (
        await api_client.put(
            f"/api/v1/profiles/{in_pid}", json={"output_languages": None}, headers=india
        )
    ).status_code == 422
    r = await api_client.post(
        "/api/v1/profiles",
        json={"region": "us", "legal_name": "Hindi US", "output_languages": ["hi"]},
        headers=us,
    )
    assert r.status_code == 422
