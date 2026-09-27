"""Object storage behind one protocol; the bucket is chosen by data residency (SPEC 10.1, 11).

    storage = get_storage_router().for_region(tenant.data_residency)
    obj = await storage.put(key, data, "application/pdf")
    url = await storage.signed_url(key)            # expires after SIGNED_URL_EXPIRES_SECONDS

Implementations: LocalStorage (dev/tests, HMAC-signed URLs), S3Storage (aiobotocore against
S3 / RustFS / MinIO), GCSStorage (google-cloud-storage, optional import). Keys come from
app.core.paths so every backend lays objects out identically.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote, urlencode, urlparse

from app.core.config import Region, Settings, StorageBackend, get_settings

DEFAULT_EXPIRES_SECONDS = 900


class StorageError(Exception):
    """Base for storage failures (missing object, backend unavailable)."""


class ObjectNotFoundError(StorageError, KeyError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    sha256: str
    bucket: str


@runtime_checkable
class Storage(Protocol):
    bucket: str

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def signed_url(self, key: str, expires_seconds: int = DEFAULT_EXPIRES_SECONDS) -> str: ...


def _validate_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\x00" in key:
        raise ValueError(f"invalid object key {key!r}")
    return key


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- local filesystem ---------------------------------------------------------------------


class LocalStorage:
    """Files under `root/<bucket>/<key>`. Signed URLs are `base_url/<key>?expires=&signature=`
    with an HMAC over (bucket, key, expires); `verify()` checks them and enforces expiry."""

    def __init__(
        self,
        root: str | Path,
        bucket: str,
        *,
        signing_secret: str,
        base_url: str = "local://files",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.bucket = bucket
        self._secret = signing_secret.encode()
        self.base_url = base_url.rstrip("/")
        self._clock = clock

    def _path(self, key: str) -> Path:
        return self.root / self.bucket / _validate_key(key)

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        await asyncio.to_thread(self._write, path, data)
        return StoredObject(
            key=key,
            size=len(data),
            content_type=content_type,
            sha256=_sha256(data),
            bucket=self.bucket,
        )

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    def _signature(self, key: str, expires: int) -> str:
        message = f"{self.bucket}\n{key}\n{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    async def signed_url(self, key: str, expires_seconds: int = DEFAULT_EXPIRES_SECONDS) -> str:
        _validate_key(key)
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be positive")
        expires = int(self._clock()) + int(expires_seconds)
        query = urlencode({"expires": expires, "signature": self._signature(key, expires)})
        return f"{self.base_url}/{quote(key, safe='/-_.~')}?{query}"

    def verify(self, url: str) -> str | None:
        """Return the key when the URL's signature is valid and unexpired, else None."""
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        try:
            expires = int(query["expires"][0])
            signature = query["signature"][0]
        except (KeyError, IndexError, ValueError):
            return None
        prefix = urlparse(self.base_url).path
        key = parsed.path[len(prefix) :].lstrip("/") if parsed.path.startswith(prefix) else ""
        from urllib.parse import unquote

        key = unquote(key)
        if not key or expires < int(self._clock()):
            return None
        if not hmac.compare_digest(signature, self._signature(key, expires)):
            return None
        return key


# --- S3-compatible (AWS S3, RustFS, MinIO) -------------------------------------------------


class S3Storage:
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        region_name: str = "us-east-1",
    ) -> None:
        from aiobotocore.config import AioConfig
        from aiobotocore.session import get_session

        self.bucket = bucket
        self._session = get_session()
        self._client_kwargs: dict[str, Any] = {
            "endpoint_url": endpoint_url or None,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region_name,
            # SigV4 + path-style: what RustFS/MinIO expect and what gives X-Amz-Expires URLs.
            "config": AioConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        }

    def _client(self) -> Any:
        return self._session.create_client("s3", **self._client_kwargs)

    async def ensure_bucket(self) -> None:
        """Create the bucket if missing (local/dev only; production buckets come from IaC)."""
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
            except s3.exceptions.ClientError:
                await s3.create_bucket(Bucket=self.bucket)

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        _validate_key(key)
        async with self._client() as s3:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return StoredObject(
            key=key,
            size=len(data),
            content_type=content_type,
            sha256=_sha256(data),
            bucket=self.bucket,
        )

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=_validate_key(key))
            except s3.exceptions.NoSuchKey as exc:
                raise ObjectNotFoundError(key) from exc
            async with response["Body"] as stream:
                body: bytes = await stream.read()
                return body

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=_validate_key(key))

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self.bucket, Key=_validate_key(key))
            except s3.exceptions.ClientError:
                return False
            return True

    async def signed_url(self, key: str, expires_seconds: int = DEFAULT_EXPIRES_SECONDS) -> str:
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be positive")
        async with self._client() as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": _validate_key(key)},
                ExpiresIn=int(expires_seconds),
            )
            return url


# --- Google Cloud Storage (production per SPEC 10.1) ---------------------------------------


class GCSStorage:
    """Minimal GCS backend. The sync client runs in a worker thread. `client` may be injected
    (tests); otherwise google-cloud-storage must be installed and ADC configured."""

    def __init__(self, bucket: str, *, client: Any | None = None) -> None:
        self.bucket = bucket
        if client is None:
            try:
                from google.cloud import storage as gcs
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise StorageError("google-cloud-storage is not installed") from exc
            client = gcs.Client()
        self._client = client

    def _blob(self, key: str) -> Any:
        return self._client.bucket(self.bucket).blob(_validate_key(key))

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        blob = self._blob(key)
        await asyncio.to_thread(blob.upload_from_string, data, content_type=content_type)
        return StoredObject(
            key=key,
            size=len(data),
            content_type=content_type,
            sha256=_sha256(data),
            bucket=self.bucket,
        )

    async def get(self, key: str) -> bytes:
        blob = self._blob(key)
        if not await asyncio.to_thread(blob.exists):
            raise ObjectNotFoundError(key)
        data: bytes = await asyncio.to_thread(blob.download_as_bytes)
        return data

    async def delete(self, key: str) -> None:
        blob = self._blob(key)
        if await asyncio.to_thread(blob.exists):
            await asyncio.to_thread(blob.delete)

    async def exists(self, key: str) -> bool:
        result: bool = await asyncio.to_thread(self._blob(key).exists)
        return result

    async def signed_url(self, key: str, expires_seconds: int = DEFAULT_EXPIRES_SECONDS) -> str:
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be positive")
        url: str = await asyncio.to_thread(
            self._blob(key).generate_signed_url,
            expiration=timedelta(seconds=int(expires_seconds)),
            version="v4",
            method="GET",
        )
        return url


# --- residency routing ---------------------------------------------------------------------


def bucket_for_region(settings: Settings, region: Region) -> str:
    backend = settings.storage_backend
    if backend is StorageBackend.GCS:
        return settings.gcs_bucket_us if region is Region.US else settings.gcs_bucket_in
    return settings.s3_bucket_us if region is Region.US else settings.s3_bucket_in


def storage_for_region(region: Region | str, settings: Settings | None = None) -> Storage:
    """Build the storage bound to the bucket of `region` (a tenant's data_residency)."""
    settings = settings or get_settings()
    region = Region(region)
    bucket = bucket_for_region(settings, region)
    backend = settings.storage_backend
    if backend is StorageBackend.LOCAL:
        return LocalStorage(
            settings.local_storage_root, bucket, signing_secret=settings.auth_secret
        )
    if backend is StorageBackend.S3:
        return S3Storage(
            bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
    return GCSStorage(bucket)


class StorageRouter:
    """One Storage per residency region, built lazily from Settings; `overrides` pin a
    region to a specific instance (tests, fakes)."""

    def __init__(
        self, settings: Settings | None = None, *, overrides: dict[Region, Storage] | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._cache: dict[Region, Storage] = dict(overrides or {})

    def for_region(self, region: Region | str) -> Storage:
        key = Region(region)
        if key not in self._cache:
            self._cache[key] = storage_for_region(key, self.settings)
        return self._cache[key]


_router: StorageRouter | None = None


def get_storage_router() -> StorageRouter:
    global _router
    if _router is None:
        _router = StorageRouter()
    return _router


def set_storage_router(router: StorageRouter | None) -> None:
    global _router
    _router = router
