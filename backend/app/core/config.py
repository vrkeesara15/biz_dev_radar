"""Application settings.

Every model ID, provider choice, quota and secret is read from the environment
here and nowhere else (CLAUDE.md). `.env.example` must list every field.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Region(StrEnum):
    US = "us"
    IN = "in"


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"
    GCS = "gcs"


class EmbeddingProviderName(StrEnum):
    VOYAGE = "voyage"
    FAKE = "fake"


class ScannerBackend(StrEnum):
    CLAMAV = "clamav"
    NOOP = "noop"


# The only place a Claude model id literal may appear (tests enforce this).
DEFAULT_OPUS_CLASS = "claude-opus-5"
DEFAULT_SONNET_CLASS = "claude-sonnet-5"
DEFAULT_HAIKU_CLASS = "claude-haiku-4-5"

# USD per million tokens (input, output, cache_read, cache_write) per model id: the LLM
# client computes cost_usd from these, never from a guess (SPEC 8 cost guard). Override
# with LLM_PRICES (JSON) when prices or model ids change.
DEFAULT_LLM_PRICES: dict[str, dict[str, float]] = {
    DEFAULT_OPUS_CLASS: {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    DEFAULT_SONNET_CLASS: {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
    DEFAULT_HAIKU_CLASS: {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # runtime
    app_env: str = "local"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # database / cache
    database_url: str = "postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar"
    database_url_owner: str = "postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar"
    redis_url: str = "redis://localhost:6380/0"
    # Celery (SPEC 10.1): eager mode runs tasks inline (tests, single-process dev); the
    # admin "run now" endpoint falls back to inline when the broker is unreachable within
    # this many seconds.
    celery_task_always_eager: bool = False
    celery_broker_connect_timeout: float = 2.0

    # files (SPEC sections 10.1, 11): one bucket per data-residency region
    storage_backend: StorageBackend = StorageBackend.LOCAL
    local_storage_root: str = ".storage"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_us: str = "bidradar-us"
    s3_bucket_in: str = "bidradar-in"
    gcs_bucket_us: str = "bidradar-us"
    gcs_bucket_in: str = "bidradar-in"
    signed_url_expires_seconds: int = 900
    # virus scanning before parsing (SPEC section 11); noop only for local/test
    scanner_backend: ScannerBackend = ScannerBackend.NOOP
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_unix_socket: str = ""
    # OCR for scanned PDF pages (SPEC 10.1: Tesseract eng+hin); none = skip pages without text
    ocr_backend: str = "none"
    ocr_languages: str = "eng+hin"
    tesseract_cmd: str = ""

    # tenancy / residency
    region: Region = Region.US

    # auth & crypto
    auth_secret: str = "dev-only-change-me-0123456789abcdef0123456789abcdef"
    # base64 of 32 random bytes (`openssl rand -base64 32`); AES-256-GCM for SPEC 11 fields
    field_encryption_key: str = "ZGV2LW9ubHktMzItYnl0ZS1rZXktY2hhbmdlLW1lISE="
    auth_rate_limit_per_minute: int = 20
    # Only enable behind a proxy that overwrites X-Forwarded-For (Cloud Run does).
    trust_proxy_headers: bool = False

    # contact / compliance
    contact_email: str = "ops@example.com"

    # third-party keys (never committed)
    sam_api_key: str = ""
    # SAM.gov key quota per UTC day (non-federal personal keys are low; see OQ-3)
    sam_daily_quota: int = 10
    # SAM.gov contract awards search (the successor of the retired ATOM feed); endpoint and
    # the NAICS list the daily job asks for are configuration, not code (OQ-40).
    sam_awards_api_url: str = "https://api.sam.gov/contract-awards/v1/search"
    # SAM.gov Entity Management API (profile autofill by UEI, SPEC 4.1)
    sam_entity_api_url: str = "https://api.sam.gov/entity-information/v3/entities"
    sam_awards_naics: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # polite HTTP client (SPEC 5.1): per-host req/s, backoff attempts, timeout
    http_default_rate_per_sec: float = 2.0
    http_gov_in_rate_per_sec: float = 1.0
    http_rate_limits: Annotated[dict[str, float], NoDecode] = Field(default_factory=dict)
    http_max_attempts: int = 5
    http_timeout_seconds: float = 30.0
    anthropic_api_key: str = ""

    # LLM model classes (SPEC section 8); ids live here only
    llm_model_opus_class: str = DEFAULT_OPUS_CLASS
    llm_model_sonnet_class: str = DEFAULT_SONNET_CLASS
    llm_model_haiku_class: str = DEFAULT_HAIKU_CLASS
    llm_model_rationale: str = DEFAULT_SONNET_CLASS
    llm_prices: Annotated[dict[str, dict[str, float]], NoDecode] = Field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_LLM_PRICES.items()}
    )
    # default output cap per call and extra attempts when the JSON output fails validation
    llm_max_tokens: int = 4096
    llm_output_retries: int = 2

    # embeddings (SPEC 10.1: configurable provider, default Voyage 1024-dim, batch embed)
    embedding_provider: EmbeddingProviderName = EmbeddingProviderName.VOYAGE
    embedding_model: str = "voyage-3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 128
    voyage_api_key: str = ""
    voyage_api_url: str = "https://api.voyageai.com/v1/embeddings"

    # money
    fx_rates: Annotated[dict[str, float], NoDecode] = Field(
        default_factory=lambda: {"USD": 1.0, "INR": 0.012}
    )

    # billing (SPEC 10.1): Stripe for us tenants (USD), Razorpay for in tenants (INR + GST).
    # *_PRICE_IDS / *_PLAN_IDS are JSON maps plan -> provider price/plan id, e.g.
    # {"pro": "price_123", "enterprise": "price_456"}.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_api_url: str = "https://api.stripe.com/v1"
    stripe_price_ids: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_api_url: str = "https://api.razorpay.com/v1"
    razorpay_plan_ids: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    # our GSTIN (supplier) and the GST rate applied to SaaS subscriptions (SAC 998314)
    billing_gstin: str = ""
    billing_gst_rate_pct: int = 18

    # seed
    seed_admin_email: str = "admin@example.com"

    # observability
    sentry_dsn: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator("cors_origins", "sam_awards_naics", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value

    @field_validator(
        "fx_rates",
        "http_rate_limits",
        "llm_prices",
        "stripe_price_ids",
        "razorpay_plan_ids",
        mode="before",
    )
    @classmethod
    def _parse_json_map(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @field_validator("embedding_dim")
    @classmethod
    def _dim_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("EMBEDDING_DIM must be positive")
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
