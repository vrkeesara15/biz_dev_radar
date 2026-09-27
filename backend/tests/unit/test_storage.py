"""M1-11: Storage protocol, local/S3/GCS backends, residency routing, signed URL expiry."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from app.core.config import Region, Settings, StorageBackend
from app.services.storage import (
    DEFAULT_EXPIRES_SECONDS,
    GCSStorage,
    LocalStorage,
    ObjectNotFoundError,
    S3Storage,
    Storage,
    StorageRouter,
    StoredObject,
    bucket_for_region,
    get_storage_router,
    set_storage_router,
    storage_for_region,
)


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def local(tmp_path: Path) -> tuple[LocalStorage, Clock]:
    clock = Clock()
    return LocalStorage(tmp_path, "bidradar-us", signing_secret="s3cret", clock=clock), clock


async def test_local_round_trip(local: tuple[LocalStorage, Clock], tmp_path: Path) -> None:
    storage, _ = local
    assert isinstance(storage, Storage)
    key = f"tenants/{uuid.uuid4()}/files/{uuid.uuid4()}.pdf"
    assert not await storage.exists(key)
    obj = await storage.put(key, b"%PDF-1.7 hello", "application/pdf")
    assert obj == StoredObject(
        key=key,
        size=14,
        content_type="application/pdf",
        sha256=obj.sha256,
        bucket="bidradar-us",
    )
    assert len(obj.sha256) == 64
    assert (tmp_path / "bidradar-us" / key).read_bytes() == b"%PDF-1.7 hello"
    assert await storage.exists(key)
    assert await storage.get(key) == b"%PDF-1.7 hello"
    await storage.delete(key)
    assert not await storage.exists(key)
    await storage.delete(key)  # idempotent
    with pytest.raises(ObjectNotFoundError):
        await storage.get(key)


@pytest.mark.parametrize("key", ["", "/abs", "a/../b", "a\x00b"])
async def test_local_rejects_unsafe_keys(local: tuple[LocalStorage, Clock], key: str) -> None:
    storage, _ = local
    with pytest.raises(ValueError):
        await storage.put(key, b"x", "text/plain")
    with pytest.raises(ValueError):
        await storage.signed_url(key)


async def test_local_signed_url_expires_after_15_minutes_by_default(
    local: tuple[LocalStorage, Clock],
) -> None:
    storage, clock = local
    key = "tenants/t/files/f.txt"
    url = await storage.signed_url(key)
    query = parse_qs(urlparse(url).query)
    assert (
        int(query["expires"][0]) == int(clock.now) + DEFAULT_EXPIRES_SECONDS == int(clock.now) + 900
    )
    assert storage.verify(url) == key
    clock.now += 899
    assert storage.verify(url) == key
    clock.now += 2
    assert storage.verify(url) is None, "URL must be rejected after expiry"
    with pytest.raises(ValueError):
        await storage.signed_url(key, expires_seconds=0)


async def test_local_signed_url_is_tamper_proof(local: tuple[LocalStorage, Clock]) -> None:
    storage, clock = local
    url = await storage.signed_url("a/b c.txt", expires_seconds=60)
    assert storage.verify(url) == "a/b c.txt"
    assert storage.verify(url.replace("expires=", "expires=9")) is None
    assert storage.verify(url.replace("b%20c", "z")) is None
    assert storage.verify(url.replace("signature=", "signature=00")) is None
    assert storage.verify("local://files/x?signature=abc") is None
    assert storage.verify("local://files/x") is None
    other = LocalStorage(storage.root, "bidradar-us", signing_secret="other", clock=clock)
    assert other.verify(url) is None
    other_bucket = LocalStorage(storage.root, "bidradar-in", signing_secret="s3cret", clock=clock)
    assert other_bucket.verify(url) is None


# --- S3 (offline: presigning needs no network) --------------------------------------------


async def test_s3_presigned_url_carries_expiry() -> None:
    storage = S3Storage(
        "bidradar-in",
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )
    url = await storage.signed_url("tenants/t/files/f.pdf")
    parsed = urlparse(url)
    assert parsed.netloc == "localhost:9000"
    assert parsed.path.startswith("/bidradar-in/tenants/t/files/f.pdf")
    query = parse_qs(parsed.query)
    assert query["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in query
    custom = await storage.signed_url("k", expires_seconds=60)
    assert parse_qs(urlparse(custom).query)["X-Amz-Expires"] == ["60"]
    with pytest.raises(ValueError):
        await storage.signed_url("k", expires_seconds=0)
    with pytest.raises(ValueError):
        await storage.put("../escape", b"x", "text/plain")


# --- GCS (fake client) ---------------------------------------------------------------------


class FakeBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self.store, self.name = store, name

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.store[self.name] = data

    def exists(self) -> bool:
        return self.name in self.store

    def download_as_bytes(self) -> bytes:
        return self.store[self.name]

    def delete(self) -> None:
        del self.store[self.name]

    def generate_signed_url(self, *, expiration: Any, version: str, method: str) -> str:
        return f"https://storage.googleapis.com/b/{self.name}?X-Goog-Expires={int(expiration.total_seconds())}&v={version}&m={method}"


class FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.store, name)


class FakeGCSClient:
    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, bytes]] = {}

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.buckets.setdefault(name, {}))


async def test_gcs_backend_with_injected_client() -> None:
    client = FakeGCSClient()
    storage = GCSStorage("bidradar-in", client=client)
    assert isinstance(storage, Storage)
    obj = await storage.put("k/1.txt", b"hi", "text/plain")
    assert obj.bucket == "bidradar-in" and obj.size == 2
    assert client.buckets["bidradar-in"]["k/1.txt"] == b"hi"
    assert await storage.exists("k/1.txt") and await storage.get("k/1.txt") == b"hi"
    url = await storage.signed_url("k/1.txt")
    assert "X-Goog-Expires=900" in url and "v=v4" in url and "m=GET" in url
    await storage.delete("k/1.txt")
    await storage.delete("k/1.txt")
    assert not await storage.exists("k/1.txt")
    with pytest.raises(ObjectNotFoundError):
        await storage.get("k/1.txt")
    with pytest.raises(ValueError):
        await storage.signed_url("k", expires_seconds=-1)


# --- residency routing ---------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_bucket_follows_data_residency_per_backend() -> None:
    s3 = _settings(storage_backend="s3", s3_bucket_us="us-b", s3_bucket_in="in-b")
    assert bucket_for_region(s3, Region.US) == "us-b"
    assert bucket_for_region(s3, Region.IN) == "in-b"
    gcs = _settings(storage_backend="gcs", gcs_bucket_us="g-us", gcs_bucket_in="g-in")
    assert bucket_for_region(gcs, Region.US) == "g-us"
    assert bucket_for_region(gcs, Region.IN) == "g-in"
    local = _settings(storage_backend="local", s3_bucket_in="in-local")
    assert bucket_for_region(local, Region.IN) == "in-local"


def test_storage_for_region_builds_the_configured_backend(tmp_path: Path) -> None:
    local = storage_for_region("in", _settings(local_storage_root=str(tmp_path)))
    assert isinstance(local, LocalStorage) and local.bucket == "bidradar-in"
    assert local.root == tmp_path
    s3 = storage_for_region(Region.US, _settings(storage_backend=StorageBackend.S3))
    assert isinstance(s3, S3Storage) and s3.bucket == "bidradar-us"
    with pytest.raises(ValueError):
        storage_for_region("eu")


def test_router_caches_per_region_and_honours_overrides(tmp_path: Path) -> None:
    settings = _settings(local_storage_root=str(tmp_path))
    router = StorageRouter(settings)
    assert router.for_region("us") is router.for_region(Region.US)
    assert router.for_region("us") is not router.for_region("in")
    fake = LocalStorage(tmp_path, "fake", signing_secret="x")
    pinned = StorageRouter(settings, overrides={Region.IN: fake})
    assert pinned.for_region("in") is fake
    set_storage_router(pinned)
    try:
        assert get_storage_router() is pinned
    finally:
        set_storage_router(None)
