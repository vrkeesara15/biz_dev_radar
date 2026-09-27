"""Embeddings (CLAUDE.md, SPEC 10.1): every vector in the system comes from an
`EmbeddingProvider` with 1024 dimensions.

    provider = embeddings_from_settings(settings)        # VoyageProvider | FakeEmbeddings
    vectors = await provider.embed(texts, input_type="document")   # len(texts) x 1024
    q = (await provider.embed([query], input_type="query"))[0]

- `VoyageProvider`: POST {VOYAGE_API_URL} in batches of EMBEDDING_BATCH_SIZE with the same
  retry policy as the polite HTTP client (full-jitter backoff on 429/5xx/transport errors,
  Retry-After honoured, HTTP_MAX_ATTEMPTS). Model id and key are Settings only.
- `FakeEmbeddings` (tests, EMBEDDING_PROVIDER=fake): deterministic hashed bag-of-words,
  L2-normalised, so cosine similarity between texts that share words is meaningful and
  the same text always maps to the same vector (no network, no randomness).

Every provider validates the response shape: one vector per text, exactly `dim` floats.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
import structlog

from app import __version__
from app.core.config import EmbeddingProviderName, Settings, get_settings
from app.core.politeness import backoff_delay, is_retryable_status, parse_retry_after

log = structlog.get_logger(__name__)

EMBEDDING_DIM = 1024
InputType = Literal["document", "query"]
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingError(RuntimeError):
    pass


@runtime_checkable
class EmbeddingProvider(Protocol):
    dim: int

    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "document"
    ) -> list[list[float]]: ...


def validate_vectors(vectors: Any, *, count: int, dim: int) -> list[list[float]]:
    """One vector per input text, each exactly `dim` finite floats."""
    if not isinstance(vectors, list) or len(vectors) != count:
        got = len(vectors) if isinstance(vectors, list) else type(vectors).__name__
        raise EmbeddingError(f"expected {count} vectors, got {got}")
    out: list[list[float]] = []
    for i, vector in enumerate(vectors):
        if not isinstance(vector, list | tuple) or len(vector) != dim:
            size = len(vector) if isinstance(vector, list | tuple) else "?"
            raise EmbeddingError(f"vector {i}: expected {dim} dimensions, got {size}")
        floats = [float(v) for v in vector]
        if any(not math.isfinite(v) for v in floats):
            raise EmbeddingError(f"vector {i}: non-finite value")
        out.append(floats)
    return out


def _l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


class FakeEmbeddings:
    """Deterministic hashed bag-of-words embeddings (tests and EMBEDDING_PROVIDER=fake).

    Each lowercase alphanumeric token lands in the bucket sha1(token) % dim (stable across
    processes, unlike hash()); the count vector is L2-normalised. An empty text gets a unit
    vector in bucket 0 so cosine distance is always defined. `calls` records every request
    so tests can assert what was (re-)embedded and with which input_type.
    """

    name = "fake"

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.calls: list[tuple[list[str], str]] = []

    @staticmethod
    def tokens(text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in self.tokens(text):
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return _l2_normalise(vector)

    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        self.calls.append((list(texts), input_type))
        return [self.vector(t) for t in texts]

    @property
    def embedded_texts(self) -> list[str]:
        return [t for texts, _ in self.calls for t in texts]


class VoyageProvider:
    """Voyage AI embeddings over the REST API (batched, retried, dimension-checked)."""

    name = "voyage"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Any] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.dim = self.settings.embedding_dim
        self.model = self.settings.embedding_model
        self.url = self.settings.voyage_api_url
        self.batch_size = max(1, self.settings.embedding_batch_size)
        self.max_attempts = max(1, self.settings.http_max_attempts)
        self._client = client
        self._sleep = sleep or asyncio.sleep
        self._rng = rng

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": f"BidRadar/{__version__}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed(
        self, texts: Sequence[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []
        if not self.settings.voyage_api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not configured")
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            out.extend(await self._embed_batch(batch, input_type))
        return out

    async def _embed_batch(self, batch: list[str], input_type: InputType) -> list[list[float]]:
        payload = {"input": batch, "model": self.model, "input_type": input_type}
        headers = {"Authorization": f"Bearer {self.settings.voyage_api_key}"}
        last: str = "no attempt"
        for attempt in range(self.max_attempts):
            try:
                response = await self.client.post(self.url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                last = repr(exc)
                log.warning("embeddings.transport_error", attempt=attempt + 1, error=last)
                await self._backoff(attempt, None)
                continue
            if is_retryable_status(response.status_code):
                last = f"HTTP {response.status_code}"
                log.warning(
                    "embeddings.retryable_status", status=response.status_code, attempt=attempt + 1
                )
                await self._backoff(attempt, response.headers.get("retry-after"))
                continue
            if response.status_code >= 400:
                raise EmbeddingError(f"voyage: HTTP {response.status_code}: {response.text[:300]}")
            return self._parse(response.json(), count=len(batch))
        raise EmbeddingError(f"voyage: gave up after {self.max_attempts} attempts ({last})")

    def _parse(self, body: Any, *, count: int) -> list[list[float]]:
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise EmbeddingError("voyage: response has no data list")
        rows = sorted(data, key=lambda d: int(d.get("index", 0)) if isinstance(d, dict) else 0)
        vectors = [row.get("embedding") if isinstance(row, dict) else None for row in rows]
        return validate_vectors(vectors, count=count, dim=self.dim)

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt + 1 >= self.max_attempts:
            return
        delay = parse_retry_after(retry_after)
        if delay is None:
            delay = backoff_delay(attempt, rng=self._rng) if self._rng else backoff_delay(attempt)
        await self._sleep(delay)


def embeddings_from_settings(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider is EmbeddingProviderName.FAKE:
        return FakeEmbeddings(settings.embedding_dim)
    return VoyageProvider(settings)


def embeddings_available(provider: EmbeddingProvider) -> bool:
    """False when the provider cannot work in this environment (Voyage without a key)."""
    if isinstance(provider, VoyageProvider):
        return bool(provider.settings.voyage_api_key)
    return True


_provider: EmbeddingProvider | None = None


def get_embeddings() -> EmbeddingProvider:
    """Process-wide provider built from Settings (tests and workers may override)."""
    global _provider
    if _provider is None:
        _provider = embeddings_from_settings()
    return _provider


def set_embeddings(provider: EmbeddingProvider | None) -> None:
    global _provider
    _provider = provider
