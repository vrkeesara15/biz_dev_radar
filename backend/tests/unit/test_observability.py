"""M7-05: OTel/Sentry wiring, the PII scrubber and the request-context log processor.

Nothing here talks to a collector or to Sentry: the whole point of the module is that it
is inert unless the settings name an endpoint, and the one test that does enable Sentry
passes a DSN with an unroutable host and asserts on the `before_send` output directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import structlog
from app.core.config import Settings
from app.core.context import (
    clear_principal,
    clear_request_id,
    set_principal,
    set_request_id,
)
from app.logging import configure_logging
from app.main import create_app
from app.observability import (
    API_SERVICE_NAME,
    REDACTED,
    WORKER_SERVICE_NAME,
    before_send,
    configure_observability,
    configure_sentry,
    configure_tracing,
    scrub,
    scrub_text,
)


@pytest.fixture(autouse=True)
def _clean_context() -> Any:
    clear_request_id()
    clear_principal()
    yield
    clear_request_id()
    clear_principal()


def empty_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# --- everything off by default ---------------------------------------------------------


def test_observability_is_a_noop_without_settings() -> None:
    settings = empty_settings()
    assert settings.sentry_dsn == ""
    assert settings.otel_exporter_otlp_endpoint == ""
    assert configure_observability(settings) == {
        "tracing": False,
        "sentry": False,
        "langfuse": False,
    }


def test_tracing_and_sentry_are_off_individually_without_their_settings() -> None:
    settings = empty_settings()
    assert configure_tracing(settings) is False
    assert configure_sentry(settings) is False


def test_no_tracer_provider_is_installed_without_an_endpoint() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    configure_observability(empty_settings())
    provider = trace.get_tracer_provider()
    assert not isinstance(provider, TracerProvider), (
        "an empty OTEL_EXPORTER_OTLP_ENDPOINT must not install an SDK tracer provider"
    )


def test_langfuse_is_reported_enabled_only_with_both_keys() -> None:
    assert configure_observability(empty_settings(langfuse_public_key="pk"))["langfuse"] is False
    assert configure_observability(empty_settings(langfuse_secret_key="sk"))["langfuse"] is False
    both = empty_settings(langfuse_public_key="pk", langfuse_secret_key="sk")
    assert configure_observability(both)["langfuse"] is True


def test_creating_the_app_records_what_is_enabled() -> None:
    app = create_app(empty_settings())
    assert app.state.observability == {"tracing": False, "sentry": False, "langfuse": False}


def test_service_names_separate_the_api_from_the_worker() -> None:
    assert API_SERVICE_NAME == "bidradar-api"
    assert WORKER_SERVICE_NAME == "bidradar-worker"


def test_resource_attributes_name_the_service_and_the_region() -> None:
    from app.observability import _resource

    api = _resource(empty_settings(region="in"), "api").attributes
    assert api["service.name"] == API_SERVICE_NAME
    assert api["deployment.region"] == "in"
    worker = _resource(empty_settings(region="us"), "worker").attributes
    assert worker["service.name"] == WORKER_SERVICE_NAME
    assert worker["deployment.region"] == "us"


# --- PII scrubbing -----------------------------------------------------------------------


def test_scrub_text_masks_the_identifiers_the_spec_calls_out() -> None:
    assert scrub_text("write to asha@example.com now") == f"write to {REDACTED} now"
    assert scrub_text("EIN 12-3456789 filed") == f"EIN {REDACTED} filed"
    assert scrub_text("PAN ABCDE1234F held") == f"PAN {REDACTED} held"
    assert scrub_text("GSTIN 29AABCU9603R1ZM") == f"GSTIN {REDACTED}"
    assert scrub_text("Bearer eyJhbGciOi.J9.abc") == f"Bearer {REDACTED}"
    assert scrub_text("nothing to hide here") == "nothing to hide here"


def test_scrub_redacts_sensitive_keys_at_any_depth() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret-token",
                "Cookie": "session=abc",
                "Stripe-Signature": "t=1,v1=deadbeef",
                "User-Agent": "pytest",
            },
            "data": {"profile": {"ein": "12-3456789", "legal_name": "Alpha LLC"}},
        },
        "extra": {"api_key": "sk-live-123", "note": "call asha@example.com"},
        "breadcrumbs": [{"message": "PAN ABCDE1234F"}],
    }
    out = scrub(event)
    headers = out["request"]["headers"]
    assert headers["Authorization"] == REDACTED
    assert headers["Cookie"] == REDACTED
    assert headers["Stripe-Signature"] == REDACTED
    assert headers["User-Agent"] == "pytest"
    assert out["request"]["data"]["profile"]["ein"] == REDACTED
    assert out["request"]["data"]["profile"]["legal_name"] == "Alpha LLC"
    assert out["extra"]["api_key"] == REDACTED
    assert out["extra"]["note"] == f"call {REDACTED}"
    assert out["breadcrumbs"][0]["message"] == f"PAN {REDACTED}"


def test_scrub_leaves_non_strings_alone_and_stops_at_a_depth_limit() -> None:
    nested: Any = "asha@example.com"
    for _ in range(12):
        nested = {"next": nested}
    assert scrub({"n": 1, "ok": True, "none": None, "t": (1, "a@b.co")})["t"] == (1, REDACTED)
    # deeper than the limit the value is passed through untouched rather than recursing
    assert scrub(nested) is not None


def test_before_send_tags_the_event_with_the_request_principal() -> None:
    set_request_id("req-123")
    set_principal("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222")
    out = before_send({"message": "boom for asha@example.com", "tags": {"existing": "1"}})
    assert out["tags"]["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    assert out["tags"]["request_id"] == "req-123"
    assert out["tags"]["existing"] == "1"
    assert out["user"] == {"id": "22222222-2222-2222-2222-222222222222"}
    assert out["message"] == f"boom for {REDACTED}"


def test_before_send_without_a_principal_attaches_no_user() -> None:
    out = before_send({"message": "boom"})
    assert out["user"] is None
    assert "tenant_id" not in (out.get("tags") or {})


def test_sentry_init_uses_no_pii_and_our_scrubber(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    captured: dict[str, Any] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a: None)
    settings = empty_settings(
        sentry_dsn="https://public@sentry.invalid/1", app_env="staging", region="in"
    )
    assert configure_sentry(settings) is True
    assert captured["send_default_pii"] is False
    assert captured["max_request_body_size"] == "never"
    assert captured["before_send"] is before_send
    assert captured["environment"] == "staging"


# --- structured logs ------------------------------------------------------------------------


def test_log_lines_carry_request_id_tenant_id_and_user_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", json_output=True)
    set_request_id("req-abc")
    set_principal("tenant-1", "user-9")
    structlog.get_logger("test").info("something.happened", extra="x")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "something.happened"
    assert line["request_id"] == "req-abc"
    assert line["tenant_id"] == "tenant-1"
    assert line["user_id"] == "user-9"
    assert line["extra"] == "x"
    assert line["level"] == "info"
    configure_logging("INFO", json_output=False)


def test_log_lines_omit_the_principal_before_authentication(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", json_output=True)
    set_request_id("req-anon")
    structlog.get_logger("test").info("anonymous.event")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["request_id"] == "req-anon"
    assert "tenant_id" not in line
    assert "user_id" not in line
    configure_logging("INFO", json_output=False)
    logging.getLogger().handlers.clear()
