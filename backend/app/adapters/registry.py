"""Registry: source_id -> adapter class.

    @register
    class SamOpportunitiesAdapter:
        source_id = "sam_opps"
        ...

Classes may set `enabled = False` (documented stubs, M2-17); the registry keeps them so
`sources` rows and health listings still exist, but the scheduler skips them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.adapters.base import SourceAdapter

_REGISTRY: dict[str, type[Any]] = {}


class AdapterNotFoundError(KeyError):
    pass


def _validate(cls: type[Any]) -> str:
    source_id = getattr(cls, "source_id", None)
    if not isinstance(source_id, str) or not source_id:
        raise TypeError(f"{cls.__name__} must define a non-empty source_id")
    if getattr(cls, "region", None) not in ("us", "in"):
        raise TypeError(f"{cls.__name__}.region must be 'us' or 'in'")
    if not isinstance(getattr(cls, "schedule", None), str):
        raise TypeError(f"{cls.__name__} must define a cron schedule string")
    for name in ("fetch", "fetch_detail", "fetch_documents", "normalize", "health"):
        if not callable(getattr(cls, name, None)):
            raise TypeError(f"{cls.__name__} does not implement SourceAdapter.{name}")
    return source_id


def register[T](cls: type[T]) -> type[T]:
    source_id = _validate(cls)
    existing = _REGISTRY.get(source_id)
    if existing is not None and existing is not cls:
        raise ValueError(f"adapter source_id {source_id!r} already registered by {existing!r}")
    _REGISTRY[source_id] = cls
    return cls


def unregister(source_id: str) -> None:
    _REGISTRY.pop(source_id, None)


def get_adapter_class(source_id: str) -> type[Any]:
    try:
        return _REGISTRY[source_id]
    except KeyError as exc:
        raise AdapterNotFoundError(source_id) from exc


def create_adapter(source_id: str, **kwargs: Any) -> SourceAdapter:
    adapter: SourceAdapter = get_adapter_class(source_id)(**kwargs)
    return adapter


def registered() -> dict[str, type[Any]]:
    """Snapshot of source_id -> class, in registration order."""
    return dict(_REGISTRY)


def is_enabled(cls: type[Any]) -> bool:
    return bool(getattr(cls, "enabled", True))


@contextmanager
def temporarily(*classes: type[Any]) -> Iterator[None]:
    """Register adapters for the duration of a block (tests)."""
    ids = [register(cls).source_id for cls in classes]
    try:
        yield
    finally:
        for source_id in ids:
            unregister(source_id)


def load_builtin_adapters() -> None:
    """Import every shipped adapter module so its @register runs. Idempotent."""
    import importlib
    import pkgutil

    import app.adapters as pkg

    for module in pkgutil.iter_modules(pkg.__path__):
        if module.name in {"base", "registry", "http"}:
            continue
        importlib.import_module(f"{pkg.__name__}.{module.name}")
