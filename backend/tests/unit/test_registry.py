"""M2-01: adapter registry."""

import pytest
from app.adapters import registry
from app.adapters.registry import AdapterNotFoundError, register, temporarily

from tests.adapters.fixture_adapter import FixtureAdapter


def test_register_and_lookup() -> None:
    with temporarily(FixtureAdapter):
        assert registry.get_adapter_class("fixture") is FixtureAdapter
        assert "fixture" in registry.registered()
        adapter = registry.create_adapter("fixture")
        assert adapter.source_id == "fixture"
        assert registry.is_enabled(FixtureAdapter)
    with pytest.raises(AdapterNotFoundError):
        registry.get_adapter_class("fixture")


def test_duplicate_source_id_rejected() -> None:
    class Other(FixtureAdapter):
        pass

    with temporarily(FixtureAdapter), pytest.raises(ValueError, match="already registered"):
        register(Other)
    # Re-registering the same class is idempotent.
    with temporarily(FixtureAdapter):
        assert register(FixtureAdapter) is FixtureAdapter


def test_register_validates_contract() -> None:
    class NoId:
        region = "us"
        schedule = "* * * * *"

    class BadRegion(FixtureAdapter):
        source_id = "bad_region"
        region = "eu"

    class Incomplete:
        source_id = "incomplete"
        region = "in"
        schedule = "* * * * *"

    for cls in (NoId, BadRegion, Incomplete):
        with pytest.raises(TypeError):
            register(cls)


def test_disabled_flag_is_read_by_registry() -> None:
    class Stub(FixtureAdapter):
        source_id = "stub"
        enabled = False

    assert registry.is_enabled(Stub) is False


def test_load_builtin_adapters_is_idempotent() -> None:
    registry.load_builtin_adapters()
    before = dict(registry.registered())
    registry.load_builtin_adapters()
    assert registry.registered() == before
    assert "fixture" not in before
