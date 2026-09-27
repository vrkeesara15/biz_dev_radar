"""Nightly live smoke (SPEC 5.1 / 12): every enabled adapter fetches >= 1 record from the
real source. Skipped unless BIDRADAR_LIVE=1 so `make smoke` is safe on laptops and in CI
without keys.

    BIDRADAR_LIVE=1 SAM_API_KEY=... python -m app.jobs.smoke [--days 7] [--only sam_opps]

Exit status 1 when any adapter raised or yielded nothing; the JSON report on stdout lists
each adapter's outcome so the Slack alert can quote it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters import registry
from app.adapters.base import SourceAdapter
from app.adapters.registry import load_builtin_adapters

LIVE_ENV = "BIDRADAR_LIVE"


@dataclass(slots=True)
class SmokeResult:
    source_id: str
    ok: bool
    records: int = 0
    health: str = ""
    message: str | None = None
    seconds: float = 0.0


def is_live() -> bool:
    return os.environ.get(LIVE_ENV, "").strip().lower() in {"1", "true", "yes"}


def smoke_adapter(adapter: SourceAdapter, *, since: datetime, max_records: int = 1) -> SmokeResult:
    started = time.monotonic()
    count = 0
    try:
        for _ in adapter.fetch(since, None):
            count += 1
            if count >= max_records:
                break
    except Exception as exc:
        return SmokeResult(
            adapter.source_id,
            ok=False,
            records=count,
            health=adapter.health().status.value,
            message=f"{type(exc).__name__}: {exc}"[:500],
            seconds=round(time.monotonic() - started, 2),
        )
    health = adapter.health()
    ok = count >= 1 and health.status.value in {"ok", "degraded"}
    return SmokeResult(
        adapter.source_id,
        ok=ok,
        records=count,
        health=health.status.value,
        message=health.message if not ok or health.status.value != "ok" else None,
        seconds=round(time.monotonic() - started, 2),
    )


def run_smoke(
    adapters: Mapping[str, type[Any]] | None = None,
    *,
    only: Iterable[str] | None = None,
    days: int = 7,
    now: datetime | None = None,
    factory: Any = None,
) -> list[SmokeResult]:
    if adapters is None:
        load_builtin_adapters()
        adapters = registry.registered()
    wanted = set(only or [])
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    results: list[SmokeResult] = []
    for source_id, cls in adapters.items():
        if wanted and source_id not in wanted:
            continue
        if not registry.is_enabled(cls):
            continue
        try:
            adapter = factory(cls) if factory else cls()
        except Exception as exc:
            results.append(SmokeResult(source_id, ok=False, message=f"construct: {exc}"[:500]))
            continue
        results.append(smoke_adapter(adapter, since=since))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="live adapter smoke test")
    parser.add_argument("--days", type=int, default=7, help="look-back window for fetch()")
    parser.add_argument("--only", action="append", default=[], help="restrict to a source id")
    args = parser.parse_args(argv)
    if not is_live():
        sys.stdout.write(
            json.dumps({"status": "skipped", "reason": f"{LIVE_ENV} is not set"}) + "\n"
        )
        return 0
    results = run_smoke(only=args.only, days=args.days)
    failed = [r for r in results if not r.ok]
    report = {
        "status": "failed" if failed else "ok",
        "checked": len(results),
        "failed": [r.source_id for r in failed],
        "results": [asdict(r) for r in results],
    }
    sys.stdout.write(json.dumps(report, indent=1, default=str) + "\n")
    return 1 if failed or not results else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
