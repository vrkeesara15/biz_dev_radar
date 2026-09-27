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


class EmailProviderName(StrEnum):
    SES = "ses"
    SENDGRID = "sendgrid"
    SMTP = "smtp"
    MEMORY = "memory"


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


# Settings whose value is a credential: they are never baked into an image, a Cloud Run
# manifest or a Terraform state. Terraform creates one Secret Manager secret per name
# (empty version) and the Cloud Run manifests reference them with secretKeyRef, so the
# deployed containers read them as ordinary env vars. `tests/unit/test_secret_settings.py`
# fails when a new credential-looking field is added without listing it here.
SECRET_SETTINGS: tuple[str, ...] = (
    "database_url",
    "database_url_owner",
    "redis_url",
    "auth_secret",
    "field_encryption_key",
    "s3_access_key",
    "s3_secret_key",
    "sam_api_key",
    "anthropic_api_key",
    "voyage_api_key",
    "sendgrid_api_key",
    "smtp_password",
    "vapid_private_key",
    "gupshup_api_key",
    "twilio_auth_token",
    "whatsapp_webhook_secret",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "razorpay_key_id",
    "razorpay_key_secret",
    "razorpay_webhook_secret",
    "sentry_dsn",
    "langfuse_public_key",
    "langfuse_secret_key",
)


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
    # Tesseract language set for documents of IN-region notices (SPEC 12: OCR hin)
    ocr_languages_in: str = "eng+hin"
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

    # notifications (SPEC 7): deep links point at the web app, one-click action links at
    # the API; failed channel sends retry this many times (backoff seconds, comma list)
    # before falling back to email.
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    notify_max_attempts: int = 3
    notify_backoff_seconds: Annotated[list[float], NoDecode] = Field(
        default_factory=lambda: [1.0, 2.0, 4.0]
    )
    # signed action links (Pursue / Watch / Pass / Assign, unsubscribe) stay valid this long
    notify_action_ttl_seconds: int = 60 * 60 * 24 * 14

    # email channel (SPEC 7): SES for production (Indian tenants via ap-south-1 so mail
    # never leaves the residency region), SendGrid as the alternative, SMTP -> Mailpit
    # locally, memory in tests.
    email_provider: EmailProviderName = EmailProviderName.SMTP
    email_from: str = "alerts@bidradar.example"
    email_from_name: str = "BidRadar"
    email_reply_to: str = ""
    # CAN-SPAM requires a physical postal address in every commercial message.
    email_postal_address: str = "BidRadar, 1 Example Street, Wilmington, DE 19801, USA"
    ses_region_us: str = "us-east-1"
    ses_region_in: str = "ap-south-1"
    sendgrid_api_key: str = ""
    sendgrid_base_url: str = "https://api.sendgrid.com"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    smtp_timeout_seconds: float = 10.0

    # WhatsApp Business through a BSP (SPEC 7, M6-05). Business-initiated messages must
    # use a PRE-APPROVED template, so the template names are configuration, never code;
    # an event with no template name here is simply not sent on WhatsApp.
    whatsapp_provider: str = ""  # gupshup | twilio | "" (off)
    whatsapp_template_deadline: str = ""
    whatsapp_template_high_match: str = ""
    whatsapp_template_language: str = "en"
    # HMAC secret for the BSP delivery-receipt webhook; empty accepts unsigned receipts
    whatsapp_webhook_secret: str = ""
    gupshup_api_key: str = ""
    gupshup_api_url: str = "https://api.gupshup.io/wa/api/v1"
    gupshup_source_number: str = ""
    gupshup_app_name: str = "BidRadar"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_api_url: str = "https://api.twilio.com"
    twilio_whatsapp_from: str = ""

    # web push (SPEC 7): VAPID key pair (RFC 8292). Generate with
    # `uv run python -c "from py_vapid import Vapid01; v=Vapid01(); v.generate_keys()"`.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:ops@example.com"

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

    # Indian portals (SPEC 2 rows 7-9, M3): captcha-free listing pages only. The CPPP
    # "tenders by organisation" page is best-effort (its layout was not verifiable at build
    # time, PROGRESS.m3.md OQ-60); organisation listings followed per run are bounded.
    cppp_by_org_url: str = "https://eprocure.gov.in/cppp/tendersbyorganisation"
    cppp_max_orgs_per_run: int = 25
    gepnic_max_orgs_per_run: int = 200
    # GeM public bid listing (JS-driven page backed by this JSON endpoint; OQ-14/OQ-61) and
    # the seller-registration link surfaced on every GeM record
    gem_bids_url: str = "https://bidplus.gem.gov.in/all-bids-data"
    gem_bid_page_url: str = "https://bidplus.gem.gov.in/showbidDocument/{bid_id}"
    gem_seller_registration_url: str = "https://gem.gov.in/register/seller/signup"
    gem_max_pages: int = 20

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

    # privacy (SPEC 11): DPDP consent notice, data-principal requests, grievance officer.
    # Bumping a *_version makes the next acceptance a new consents row (the old one stays).
    dpdp_notice_version: str = "v1"
    privacy_policy_version: str = "v1"
    terms_version: str = "v1"
    # statutory answer-by window for a data-principal request, in days from receipt
    data_request_sla_days: int = 30
    grievance_officer_name: str = ""
    grievance_officer_email: str = ""

    # seed
    seed_admin_email: str = "admin@example.com"

    # observability (SPEC 10.1). Every one of these is off when empty: no OTLP endpoint
    # means no tracer provider, no DSN means no Sentry, no Langfuse keys mean a NoopTracer.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    # OTLP/HTTP collector base URL, e.g. http://localhost:4318 (Cloud Trace via the agent)
    otel_exporter_otlp_endpoint: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator("cors_origins", "sam_awards_naics", "notify_backoff_seconds", mode="before")
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
