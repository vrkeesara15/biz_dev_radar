"""M2-17: documented stubs and paid-feed base are registered, disabled, not_implemented."""

from datetime import UTC, datetime

import pytest
from app.adapters import registry
from app.adapters.base import AdapterStatus, RawRecord, SourceAdapter
from app.adapters.paid_feeds import (
    PAID_FEEDS,
    EnvSecretResolver,
    HigherGovAdapter,
    PaidFeedAdapter,
    resolver_for,
)
from app.adapters.registry import load_builtin_adapters
from app.adapters.stubs import (
    DefenseGovAwardsAdapter,
    DefprocAdapter,
    IrepsAdapter,
    SledGenericAdapter,
    StubAdapter,
)
from app.celery_app import build_beat_schedule
from app.core.attribution import source_name

STUBS = (DefenseGovAwardsAdapter, SledGenericAdapter, IrepsAdapter, DefprocAdapter, *PAID_FEEDS)
STUB_IDS = {
    "defense_gov_awards",
    "sled_generic",
    "ireps",
    "defproc",
    "highergov",
    "govspend",
    "bidnet",
    "tendertiger",
    "tender247",
    "bidassist",
}


def test_stubs_are_registered_disabled_and_satisfy_the_protocol() -> None:
    load_builtin_adapters()
    registered = registry.registered()
    assert set(registered) >= STUB_IDS
    for cls in STUBS:
        assert registered[cls.source_id] is cls
        assert registry.is_enabled(cls) is False
        adapter = cls()
        assert isinstance(adapter, SourceAdapter)
        assert cls.region in ("us", "in") and len(cls.schedule.split()) == 5
        assert cls.display_name and cls.portal_url.startswith("https://")
        assert source_name(cls.source_id) != cls.source_id  # attribution knows every stub
    # the enabled built-ins are untouched
    assert {"sam_opps", "grants_gov", "usaspending", "sam_awards"} <= {
        sid for sid, cls in registered.items() if registry.is_enabled(cls)
    }


@pytest.mark.parametrize("cls", STUBS, ids=lambda c: c.source_id)
def test_stub_health_and_methods(cls: type[StubAdapter]) -> None:
    adapter = cls()
    health = adapter.health()
    assert health.status is AdapterStatus.NOT_IMPLEMENTED
    assert health.message and cls.display_name in health.message
    assert list(adapter.fetch(datetime(2026, 1, 1, tzinfo=UTC), None)) == []
    raw = RawRecord(
        source_id=cls.source_id, external_id="x", fetched_at=datetime.now(UTC), payload={}
    )
    assert adapter.fetch_documents(raw) == []
    with pytest.raises(NotImplementedError, match=r"docs/adapters\.md"):
        adapter.normalize(raw)
    with pytest.raises(NotImplementedError):
        adapter.fetch_detail("x")


def test_stubs_are_excluded_from_the_beat_schedule() -> None:
    schedule = build_beat_schedule()
    assert not any(key.removeprefix("source:") in STUB_IDS for key in schedule)


class FakeResolver:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = secrets
        self.calls: list[str] = []

    def resolve(self, ref: str) -> str | None:
        self.calls.append(ref)
        return self.secrets.get(ref)


def test_paid_feed_licence_by_reference_never_by_value(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = FakeResolver({"projects/p/secrets/highergov/versions/1": "sk-live-SECRET"})
    adapter = HigherGovAdapter(
        licence_secret_ref="projects/p/secrets/highergov/versions/1",
        resolver=resolver,
        tenant_id="t-1",
    )
    assert adapter.has_licence and adapter.licence_key() == "sk-live-SECRET"
    assert resolver.calls == ["projects/p/secrets/highergov/versions/1"]  # resolved once
    adapter.licence_key()
    assert len(resolver.calls) == 1
    health = adapter.health()
    assert health.status is AdapterStatus.NOT_IMPLEMENTED
    assert "SECRET" not in (health.message or "") and "SECRET" not in repr(adapter)
    assert "licence reference set" in (health.message or "")
    # no reference at all: no licence, still a well-behaved adapter
    monkeypatch.delenv("GOVSPEND_API_KEY", raising=False)
    bare = PAID_FEEDS[1]()
    assert bare.licence_secret_ref == "GOVSPEND_API_KEY" and not bare.has_licence
    assert "no licence reference" not in (bare.health().message or "")  # env ref is a reference
    none = PaidFeedAdapter(licence_secret_ref=None)
    assert none.licence_secret_ref is None and not none.has_licence
    assert "no licence reference" in (none.health().message or "")


def test_env_resolver_and_resolver_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENDER247_API_KEY", "from-env")
    assert EnvSecretResolver().resolve("TENDER247_API_KEY") == "from-env"
    assert EnvSecretResolver().resolve("MISSING_KEY_X") is None
    assert isinstance(resolver_for("TENDER247_API_KEY"), EnvSecretResolver)
    assert (
        type(resolver_for("projects/p/secrets/x/versions/latest")).__name__ == "GcpSecretResolver"
    )
    assert isinstance(resolver_for(None), EnvSecretResolver)
    adapter = PAID_FEEDS[4]()  # tender247 with the default env reference
    assert adapter.has_licence and adapter.licence_key() == "from-env"
