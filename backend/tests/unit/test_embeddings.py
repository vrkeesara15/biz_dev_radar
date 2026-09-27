"""M1-12: EmbeddingProvider protocol, FakeEmbeddings determinism, VoyageProvider over respx."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest
import respx
from app.core.config import Settings
from app.services.embeddings import (
    EMBEDDING_DIM,
    EmbeddingError,
    EmbeddingProvider,
    FakeEmbeddings,
    VoyageProvider,
    embeddings_available,
    embeddings_from_settings,
    validate_vectors,
)

URL = "https://api.voyageai.com/v1/embeddings"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"voyage_api_key": "vk-test", "http_max_attempts": 3}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _voyage_body(count: int, dim: int = EMBEDDING_DIM) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": [0.1] * dim, "index": i} for i in range(count)
        ],
        "model": "voyage-3",
        "usage": {"total_tokens": 10 * count},
    }


# --- FakeEmbeddings -----------------------------------------------------------------------------


async def test_fake_embeddings_are_1024_dim_unit_vectors_and_deterministic() -> None:
    fake = FakeEmbeddings()
    assert isinstance(fake, EmbeddingProvider)
    first = await fake.embed(["Cloud migration services for federal agencies", ""])
    second = await FakeEmbeddings().embed(["Cloud migration services for federal agencies", ""])
    assert first == second
    assert len(first) == 2 and all(len(v) == EMBEDDING_DIM for v in first)
    for vector in first:
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)
    assert fake.calls == [(["Cloud migration services for federal agencies", ""], "document")]


async def test_fake_embeddings_cosine_is_meaningful() -> None:
    fake = FakeEmbeddings()
    cloud, cloud_again, paving = await fake.embed(
        [
            "Cloud migration and FedRAMP hosting services",
            "FedRAMP cloud hosting and migration",
            "Asphalt paving and road resurfacing",
        ]
    )
    assert _cosine(cloud, cloud_again) > 0.5 > _cosine(cloud, paving)
    query = (await fake.embed(["cloud migration"], input_type="query"))[0]
    assert _cosine(query, cloud) > _cosine(query, paving)
    assert fake.calls[-1][1] == "query"


def test_validate_vectors_rejects_bad_shapes() -> None:
    good = validate_vectors([[0.5] * 4], count=1, dim=4)
    assert good == [[0.5] * 4]
    with pytest.raises(EmbeddingError, match="expected 2 vectors"):
        validate_vectors([[0.5] * 4], count=2, dim=4)
    with pytest.raises(EmbeddingError, match="expected 4 dimensions, got 3"):
        validate_vectors([[0.5] * 3], count=1, dim=4)
    with pytest.raises(EmbeddingError, match="non-finite"):
        validate_vectors([[float("nan")] * 4], count=1, dim=4)
    with pytest.raises(EmbeddingError):
        validate_vectors({"not": "a list"}, count=1, dim=4)


# --- VoyageProvider -----------------------------------------------------------------------------


@respx.mock
async def test_voyage_batches_of_128_with_model_key_and_input_type() -> None:
    route = respx.post(URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=_voyage_body(len(json.loads(request.content)["input"]))
        )
    )
    provider = VoyageProvider(_settings(embedding_model="voyage-3"))
    texts = [f"text {i}" for i in range(130)]
    vectors = await provider.embed(texts, input_type="document")
    assert len(vectors) == 130 and all(len(v) == EMBEDDING_DIM for v in vectors)
    assert route.call_count == 2
    first, second = route.calls[0].request, route.calls[1].request
    assert first.headers["authorization"] == "Bearer vk-test"
    body = json.loads(first.content)
    assert body["model"] == "voyage-3" and body["input_type"] == "document"
    assert len(body["input"]) == 128 and len(json.loads(second.content)["input"]) == 2
    await provider.aclose()


@respx.mock
async def test_voyage_orders_by_index_and_supports_query_input_type() -> None:
    body = _voyage_body(2)
    body["data"][0]["embedding"] = [1.0] * EMBEDDING_DIM
    body["data"] = list(reversed(body["data"]))  # index 1 first, index 0 second
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    provider = VoyageProvider(_settings())
    vectors = await provider.embed(["a", "b"], input_type="query")
    assert vectors[0][0] == 1.0 and vectors[1][0] == 0.1
    assert json.loads(respx.calls.last.request.content)["input_type"] == "query"
    assert await provider.embed([]) == []


@respx.mock
async def test_voyage_backs_off_on_429_and_5xx_then_succeeds() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(503),
            httpx.Response(200, json=_voyage_body(1)),
        ]
    )
    provider = VoyageProvider(_settings(), sleep=sleep, rng=lambda: 0.5)
    vectors = await provider.embed(["x"])
    assert len(vectors) == 1 and route.call_count == 3
    assert sleeps == [2.0, 1.0]  # Retry-After honoured, then full-jitter 0.5 * 2**1


@respx.mock
async def test_voyage_gives_up_after_max_attempts_and_rejects_bad_dims() -> None:
    respx.post(URL).mock(return_value=httpx.Response(500))
    provider = VoyageProvider(_settings(http_max_attempts=2), sleep=lambda s: _noop())
    with pytest.raises(EmbeddingError, match="gave up after 2 attempts"):
        await provider.embed(["x"])
    respx.post(URL).mock(return_value=httpx.Response(200, json=_voyage_body(1, dim=768)))
    with pytest.raises(EmbeddingError, match="expected 1024 dimensions"):
        await provider.embed(["x"])
    respx.post(URL).mock(return_value=httpx.Response(401, json={"detail": "bad key"}))
    with pytest.raises(EmbeddingError, match="HTTP 401"):
        await provider.embed(["x"])


async def _noop() -> None:
    return None


async def test_voyage_without_key_fails_fast_without_network() -> None:
    provider = VoyageProvider(_settings(voyage_api_key=""))
    assert embeddings_available(provider) is False
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        await provider.embed(["x"])


def test_provider_choice_comes_from_settings() -> None:
    voyage = embeddings_from_settings(_settings(embedding_provider="voyage"))
    assert isinstance(voyage, VoyageProvider) and voyage.dim == 1024
    assert voyage.model == "voyage-3" and voyage.url == URL and voyage.batch_size == 128
    fake = embeddings_from_settings(_settings(embedding_provider="fake"))
    assert isinstance(fake, FakeEmbeddings) and embeddings_available(fake)
    assert Settings(_env_file=None).embedding_provider == "voyage"  # type: ignore[call-arg]
