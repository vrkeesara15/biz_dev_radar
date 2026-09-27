"""M7-01/M7-02: SECRET_SETTINGS is the single list of credentials, and it is complete.

Terraform creates one Secret Manager entry per name and the Cloud Run manifests reference
them with secretKeyRef, so a credential missing from this list silently becomes a plain
env var in a manifest. The heuristic below is the safety net: add the field to
SECRET_SETTINGS, or to NOT_SECRET with a reason.
"""

from __future__ import annotations

from app.core.config import SECRET_SETTINGS, Settings

# Fields whose name looks credential-ish but whose value is public configuration.
NOT_SECRET: dict[str, str] = {
    "stripe_price_ids": "public price identifiers, safe in a manifest",
    "razorpay_plan_ids": "public plan identifiers, safe in a manifest",
    "s3_endpoint_url": "an endpoint, not a credential",
    "sam_awards_api_url": "a public endpoint",
    "sam_entity_api_url": "a public endpoint",
    "stripe_api_url": "a public endpoint",
    "razorpay_api_url": "a public endpoint",
    "voyage_api_url": "a public endpoint",
    "langfuse_host": "a public endpoint",
    "vapid_public_key": "M4-12: published to browsers so they can subscribe",
    "sendgrid_base_url": "a public endpoint",
    "otel_exporter_otlp_endpoint": "a collector address, not a credential",
    "local_storage_root": "a filesystem path",
    "auth_rate_limit_per_minute": "a number",
    "llm_max_tokens": "an output cap, matched only by the 'token' marker",
}

_SUSPICIOUS = ("secret", "password", "_key", "_dsn", "token", "credential")
# Credentials whose name gives nothing away.
_EXTRA = ("database_url", "database_url_owner", "redis_url", "razorpay_key_id")


def _looks_like_a_secret(name: str) -> bool:
    return name in _EXTRA or any(marker in name for marker in _SUSPICIOUS)


def test_every_listed_name_is_a_real_setting() -> None:
    unknown = set(SECRET_SETTINGS) - set(Settings.model_fields)
    assert not unknown, f"SECRET_SETTINGS names fields that do not exist: {sorted(unknown)}"


def test_list_has_no_duplicates() -> None:
    assert len(SECRET_SETTINGS) == len(set(SECRET_SETTINGS))


def test_no_credential_looking_field_is_missing() -> None:
    missing = sorted(
        name
        for name in Settings.model_fields
        if _looks_like_a_secret(name) and name not in SECRET_SETTINGS and name not in NOT_SECRET
    )
    assert not missing, (
        "these settings look like credentials but are not in SECRET_SETTINGS "
        f"(add them, or add them to NOT_SECRET with a reason): {missing}"
    )


def test_defaults_of_secret_settings_are_never_real_values() -> None:
    """A committed default must be empty or an obvious dev placeholder."""
    defaults = Settings()
    for name in SECRET_SETTINGS:
        value = str(getattr(defaults, name))
        if not value:
            continue
        assert any(
            marker in value
            for marker in ("localhost", "dev-only", "bidradar", "minioadmin", "ZGV2LW9ubHk")
        ), f"{name} has a suspicious committed default"
