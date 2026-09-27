"""Polite HTTP client: the ONLY way adapters may reach the network (CLAUDE.md, SPEC 5.1).

- per-host token bucket (1 req/s for *.gov.in, configurable per host, SAM daily key quota)
- exponential backoff with full jitter on 429/5xx/transport errors, up to `max_attempts`,
  honouring Retry-After
- identifying User-Agent `BidRadar/<version> (+mailto:<CONTACT_EMAIL>)`
- robots.txt fetched and cached per host; a disallowed URL raises RobotsDisallowedError
  before any request is made (never bypassed)
- every response body archived under raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at}
  through an `Archiver`; the key comes back as `PoliteResponse.raw_ref`

The client is synchronous (adapters are plain generators). `StorageArchiver` bridges to the
async `Storage` protocol and works whether or not an event loop is running in the thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from app import __version__
from app.core.config import Settings, get_settings
from app.core.paths import raw_archive_key
from app.core.politeness import (
    DailyQuota,
    PolicyTable,
    TokenBucket,
    backoff_delay,
    is_retryable_status,
    parse_retry_after,
    user_agent,
)
from app.services.storage import Storage

log = structlog.get_logger(__name__)

ROBOTS_AGENT = "BidRadar"


class PoliteClientError(Exception):
    pass


class RobotsDisallowedError(PoliteClientError):
    def __init__(self, url: str) -> None:
        super().__init__(f"robots.txt disallows {url}")
        self.url = url


class QuotaExhaustedError(PoliteClientError):
    def __init__(self, host: str, limit: int) -> None:
        super().__init__(f"daily quota of {limit} requests exhausted for {host}")
        self.host = host
        self.limit = limit


class RetryExhaustedError(PoliteClientError):
    def __init__(self, url: str, attempts: int, last: httpx.Response | Exception) -> None:
        detail = f"HTTP {last.status_code}" if isinstance(last, httpx.Response) else repr(last)
        super().__init__(f"{url}: gave up after {attempts} attempts ({detail})")
        self.url = url
        self.attempts = attempts
        self.last = last


# --- archive -------------------------------------------------------------------------------


class Archiver(Protocol):
    def store(self, key: str, data: bytes, content_type: str) -> str: ...


class MemoryArchiver:
    """Keeps bodies in a dict (tests)."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def store(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = (data, content_type)
        return key


class NullArchiver:
    def store(self, key: str, data: bytes, content_type: str) -> str:
        return key


class StorageArchiver:
    """Sync facade over the async Storage protocol.

    In a thread without a running loop the coroutine is run directly; when called from
    inside a running loop (an adapter driven synchronously inside the async pipeline) the
    put runs on a helper thread with its own loop so we never re-enter the caller's loop.
    """

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="raw-archive"
        )

    def store(self, key: str, data: bytes, content_type: str) -> str:
        coro = self.storage.put(key, data, content_type)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            stored = asyncio.run(coro)
        else:
            stored = self._pool.submit(asyncio.run, coro).result()
        return stored.key


# --- client --------------------------------------------------------------------------------


@dataclass(slots=True)
class PoliteResponse:
    response: httpx.Response
    raw_ref: str | None
    fetched_at: datetime
    attempts: int

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def content(self) -> bytes:
        return self.response.content

    @property
    def text(self) -> str:
        return self.response.text

    def json(self) -> Any:
        return self.response.json()

    @property
    def content_type(self) -> str:
        return str(self.response.headers.get("content-type", "application/octet-stream"))


def policy_table_from_settings(settings: Settings) -> PolicyTable:
    return PolicyTable(
        default_rate=settings.http_default_rate_per_sec,
        gov_in_rate=settings.http_gov_in_rate_per_sec,
        per_host=dict(settings.http_rate_limits),
        quotas={"api.sam.gov": settings.sam_daily_quota},
    )


class PoliteClient:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        archiver: Archiver | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] | None = None,
        policies: PolicyTable | None = None,
        max_attempts: int | None = None,
        timeout: float | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.archiver: Archiver = archiver or NullArchiver()
        self.policies = policies or policy_table_from_settings(self.settings)
        self.max_attempts = max_attempts or self.settings.http_max_attempts
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._now = now or (lambda: datetime.now(UTC))
        self.user_agent = user_agent(__version__, self.settings.contact_email)
        self._buckets: dict[str, TokenBucket] = {}
        self._quotas: dict[str, DailyQuota] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout or self.settings.http_timeout_seconds,
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
            follow_redirects=True,
        )

    # -- lifecycle
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- politeness
    def _bucket(self, host: str) -> TokenBucket:
        if host not in self._buckets:
            policy = self.policies.for_host(host)
            self._buckets[host] = TokenBucket(policy.rate_per_sec, policy.burst, clock=self._clock)
        return self._buckets[host]

    def _quota(self, host: str) -> DailyQuota | None:
        policy = self.policies.for_host(host)
        if policy.daily_quota is None:
            return None
        if host not in self._quotas:
            self._quotas[host] = DailyQuota(policy.daily_quota, clock=self._clock)
        return self._quotas[host]

    def remaining_quota(self, host: str) -> int | None:
        quota = self._quota(host)
        return None if quota is None else quota.remaining

    def _throttle(self, host: str) -> None:
        wait = self._bucket(host).acquire()
        if wait > 0:
            log.debug("http.throttle", host=host, wait=round(wait, 3))
            self._sleep(wait)

    def _take_quota(self, host: str) -> None:
        quota = self._quota(host)
        if quota is not None and not quota.take():
            raise QuotaExhaustedError(host, quota.limit)

    # -- robots.txt
    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        """Parsed robots.txt for host, cached. None = no restrictions known."""
        if host in self._robots:
            return self._robots[host]
        url = f"{scheme}://{host}/robots.txt"
        parser = RobotFileParser(url)
        try:
            self._throttle(host)
            response = self._http.get(url)
        except httpx.HTTPError as exc:
            # Unreachable robots: treat as unknown for now and retry next time.
            log.warning("robots.unreachable", host=host, error=str(exc))
            return None
        if response.status_code in (401, 403):
            parser.disallow_all = True  # type: ignore[attr-defined]
        elif response.status_code >= 500:
            # Temporary failure: be conservative this time but do not cache.
            log.warning("robots.server_error", host=host, status=response.status_code)
            parser.disallow_all = True  # type: ignore[attr-defined]
            return parser
        elif response.status_code >= 400:
            parser.allow_all = True  # type: ignore[attr-defined]  # runtime attr, not in typeshed
        else:
            parser.parse(response.text.splitlines())
        self._robots[host] = parser
        return parser

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        parser = self._robots_for(parts.scheme or "https", parts.netloc)
        if parser is None:
            return True
        return parser.can_fetch(ROBOTS_AGENT, url) and parser.can_fetch(self.user_agent, url)

    # -- requests
    def request(
        self,
        method: str,
        url: str,
        *,
        source_id: str,
        external_id: str,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        archive: bool = True,
        respect_robots: bool = True,
    ) -> PoliteResponse:
        host = urlsplit(url).netloc.lower()
        if respect_robots and not self.allowed_by_robots(url):
            raise RobotsDisallowedError(url)
        last: httpx.Response | Exception | None = None
        for attempt in range(self.max_attempts):
            self._throttle(host)
            self._take_quota(host)
            fetched_at = self._now()
            try:
                response = self._http.request(
                    method, url, params=params, json=json, data=data, headers=headers
                )
            except httpx.TransportError as exc:
                last = exc
                log.warning("http.transport_error", url=url, attempt=attempt + 1, error=str(exc))
                self._backoff(attempt, None)
                continue
            if is_retryable_status(response.status_code):
                last = response
                log.warning(
                    "http.retryable_status",
                    url=url,
                    status=response.status_code,
                    attempt=attempt + 1,
                )
                self._backoff(attempt, response.headers.get("retry-after"))
                continue
            raw_ref = None
            if archive:
                key = raw_archive_key(source_id, external_id, fetched_at)
                content_type = response.headers.get("content-type", "application/octet-stream")
                raw_ref = self.archiver.store(key, response.content, content_type)
            return PoliteResponse(
                response=response, raw_ref=raw_ref, fetched_at=fetched_at, attempts=attempt + 1
            )
        assert last is not None
        raise RetryExhaustedError(url, self.max_attempts, last)

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt + 1 >= self.max_attempts:
            return  # no sleep after the final attempt
        delay = parse_retry_after(retry_after, now=self._now())
        if delay is None:
            delay = backoff_delay(attempt, rng=self._rng) if self._rng else backoff_delay(attempt)
        self._sleep(delay)

    def get(self, url: str, **kwargs: Any) -> PoliteResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> PoliteResponse:
        return self.request("POST", url, **kwargs)
