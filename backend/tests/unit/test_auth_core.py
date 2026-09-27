"""M0-07: JWT claims (pure)."""

import time
import uuid

import jwt
import pytest
from app.core.auth import (
    AuthClaims,
    ExpiredTokenError,
    InvalidTokenError,
    decode_token,
    encode_token,
    parse_bearer,
)
from app.core.roles import Role

SECRET = "s3cret-s3cret-s3cret-s3cret-s3cret-s3cret"


def _mint(**kwargs) -> str:  # type: ignore[no-untyped-def]
    defaults = dict(
        user_id=uuid.uuid4(),
        email="a@example.com",
        tenant_id=uuid.uuid4(),
        role=Role.WRITER,
        secret=SECRET,
    )
    defaults.update(kwargs)
    return encode_token(**defaults)  # type: ignore[arg-type]


def test_roundtrip() -> None:
    uid, tid = uuid.uuid4(), uuid.uuid4()
    claims = decode_token(_mint(user_id=uid, tenant_id=tid, role="reviewer"), SECRET)
    assert isinstance(claims, AuthClaims)
    assert claims.sub == uid and claims.tenant_id == tid and claims.role is Role.REVIEWER
    assert claims.exp > time.time()


def test_wrong_secret_and_garbage() -> None:
    with pytest.raises(InvalidTokenError):
        decode_token(_mint(), "other-other-other-other-other-other-oth")
    with pytest.raises(InvalidTokenError):
        decode_token("not.a.jwt", SECRET)
    with pytest.raises(InvalidTokenError):
        decode_token("", SECRET)
    with pytest.raises(InvalidTokenError):
        decode_token(_mint(), "")


def test_expired() -> None:
    token = _mint(expires_in=60, now=time.time() - 3600)
    with pytest.raises(ExpiredTokenError):
        decode_token(token, SECRET)


def test_missing_or_malformed_claims() -> None:
    now = int(time.time())
    no_tenant = jwt.encode(
        {"sub": str(uuid.uuid4()), "email": "x", "role": "viewer", "exp": now + 60},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(no_tenant, SECRET)
    bad_role = _mint(extra={"role": "superuser"})
    with pytest.raises(InvalidTokenError, match="malformed"):
        decode_token(bad_role, SECRET)
    bad_uuid = _mint(extra={"tenant_id": "nope"})
    with pytest.raises(InvalidTokenError):
        decode_token(bad_uuid, SECRET)


def test_alg_none_rejected() -> None:
    payload = {
        "sub": str(uuid.uuid4()),
        "email": "x",
        "tenant_id": str(uuid.uuid4()),
        "role": "viewer",
        "exp": int(time.time()) + 60,
    }
    token = jwt.encode(payload, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(InvalidTokenError):
        decode_token(token, SECRET)


def test_parse_bearer() -> None:
    assert parse_bearer(None) is None
    assert parse_bearer("") is None
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("Bearer ") is None
    assert parse_bearer("bearer abc.def") == "abc.def"
    assert parse_bearer("  Bearer   tok  ") == "tok"
