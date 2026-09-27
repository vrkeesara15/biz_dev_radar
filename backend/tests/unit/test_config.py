"""M0-01/M0-03: settings load from env; model ids and providers are settings."""

import pytest
from app.core.config import (
    DEFAULT_HAIKU_CLASS,
    DEFAULT_OPUS_CLASS,
    DEFAULT_SONNET_CLASS,
    EmbeddingProviderName,
    Region,
    Settings,
    StorageBackend,
    get_settings,
)
from pydantic import ValidationError


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_defaults() -> None:
    s = _settings()
    assert s.region is Region.US
    assert s.storage_backend is StorageBackend.LOCAL
    assert s.embedding_provider is EmbeddingProviderName.VOYAGE
    assert s.embedding_dim == 1024
    assert s.llm_model_opus_class == DEFAULT_OPUS_CLASS
    assert s.llm_model_sonnet_class == DEFAULT_SONNET_CLASS
    assert s.llm_model_haiku_class == DEFAULT_HAIKU_CLASS
    assert s.llm_model_rationale == DEFAULT_SONNET_CLASS
    assert s.auth_rate_limit_per_minute == 20
    assert not s.is_production


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGION", "in")
    monkeypatch.setenv("LLM_MODEL_OPUS_CLASS", "opus-override")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("APP_ENV", "production")
    s = _settings()
    assert s.region is Region.IN
    assert s.llm_model_opus_class == "opus-override"
    assert s.embedding_provider is EmbeddingProviderName.FAKE
    assert s.storage_backend is StorageBackend.S3
    assert s.is_production


def test_fx_rates_and_cors_parse_from_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FX_RATES", '{"USD": 1.0, "INR": 0.011}')
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    s = _settings()
    assert s.fx_rates == {"USD": 1.0, "INR": 0.011}
    assert s.cors_origins == ["http://a.test", "http://b.test"]
    monkeypatch.setenv("CORS_ORIGINS", '["http://c.test"]')
    assert _settings().cors_origins == ["http://c.test"]


def test_invalid_region_and_dim_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGION", "eu")
    with pytest.raises(ValidationError):
        _settings()
    monkeypatch.delenv("REGION")
    monkeypatch.setenv("EMBEDDING_DIM", "0")
    with pytest.raises(ValidationError):
        _settings()


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
