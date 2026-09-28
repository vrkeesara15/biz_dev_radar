"""The merged route table has exactly one handler per (method, path).

The M5 workspace API and the M6 collaboration API both mounted
`/pursuits/{id}/comments` and `PATCH /pursuits/{id}`; FastAPI would serve the first
match and silently ignore the second, so the reconciliation is pinned by a test rather
than by having looked at the file once.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.config import Settings
from app.main import create_app


def _walk(routes: list[Any], base: str, out: list[tuple[str, str]]) -> None:
    """Every (method, path) the app actually serves.

    FastAPI keeps an included router nested behind `_IncludedRouter` rather than copying
    its routes up. A route's own `path` carries its router's prefix, and the include
    context carries everything above it, so the two together are the served path.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = getattr(route, "include_context", None)
            _walk(list(inner.routes), getattr(context, "prefix", base), out)
            continue
        for method in getattr(route, "methods", None) or ():
            out.append((method.upper(), base + str(getattr(route, "path", ""))))


def _pairs() -> list[tuple[str, str]]:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    out: list[tuple[str, str]] = []
    _walk(list(app.routes), "", out)
    assert len(out) > 100, f"the walker found only {len(out)} routes; it stopped too early"
    assert ("GET", "/api/v1/pursuits") in out, "the walker lost the /api/v1 prefix"
    return out


def test_no_route_is_registered_twice() -> None:
    duplicates = sorted(pair for pair, count in Counter(_pairs()).items() if count > 1)
    assert duplicates == [], f"two handlers claim the same route: {duplicates}"


def test_the_reconciled_pursuit_routes_are_all_mounted_once() -> None:
    """Tasks, comments, the two gates and the exports, after the m5b merge."""
    counts = Counter(_pairs())
    expected = [
        ("GET", "/api/v1/pursuits/{pursuit_id}/tasks"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/tasks"),
        ("PATCH", "/api/v1/pursuits/{pursuit_id}/tasks/{task_id}"),
        ("DELETE", "/api/v1/pursuits/{pursuit_id}/tasks/{task_id}"),
        ("GET", "/api/v1/pursuits/{pursuit_id}/comments"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/comments"),
        ("PATCH", "/api/v1/pursuits/{pursuit_id}/comments/{comment_id}"),
        ("DELETE", "/api/v1/pursuits/{pursuit_id}/comments/{comment_id}"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/comments/{comment_id}/resolve"),
        ("PATCH", "/api/v1/pursuits/{pursuit_id}"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/decision"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/approve-package"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/mark-final"),
        ("POST", "/api/v1/pursuits/{pursuit_id}/export"),
        ("GET", "/api/v1/pursuits/{pursuit_id}/exports"),
        ("GET", "/api/v1/pursuits/{pursuit_id}/exports/{export_id}"),
    ]
    assert [counts[pair] for pair in expected] == [1] * len(expected), {
        pair: counts[pair] for pair in expected if counts[pair] != 1
    }
