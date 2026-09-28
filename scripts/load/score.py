#!/usr/bin/env python3
"""M7-10 / SPEC 12: time the batch scorer over the whole seeded corpus.

    python scripts/load/score.py                  # the SPEC gate: 50k x 200 in < 600 s
    python scripts/load/score.py --scale 0.1      # the CI smoke, with a scaled guard
    python scripts/load/score.py --workers 4      # pin the pool size

How it runs
  `MatchScorer.score_batch()` walks the tenants one at a time in a single process, which
  OQ-95 measured at ~1,800 pairs/s — 50k x 200 = 10 M pairs would take ~90 minutes that
  way. Profiles are embarrassingly parallel, so this script shards them round-robin over
  a `spawn` process pool and calls `MatchScorer.rescore_profile` per profile, which is
  the same stage 1 + stage 2 path with the same SQL prefilter and the same per-page
  upsert. Stage 3 (the LLM rationale) is deliberately not part of the measurement.

What it reports
  nominal pairs (profiles x notices, the number SPEC 12 names), the pairs that actually
  reached stage 2 after the SQL prefilter, both throughputs, per-stage timings, the band
  histogram and the extrapolation to full scale.

Exit codes: 0 inside the budget · 1 over the budget (or a worker failed) · 2 bad usage.
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from loadlib import (
    FULL_OPPORTUNITIES,
    FULL_PROFILES,
    MIN_SCALED_BUDGET_SECONDS,
    SCORE_BUDGET_SECONDS,
    Stopwatch,
    Totals,
    build_database,
    build_settings,
    database_arguments,
    database_name,
    emit,
    quiet_logging,
    scale_argument,
    score_shard,
    shards,
    write_report,
)

FULL_PAIRS = FULL_PROFILES * FULL_OPPORTUNITIES
DEFAULT_PAGE_SIZE = 500


def default_workers() -> int:
    """Leave a couple of cores for Postgres, which is the other half of this benchmark."""
    return max(1, (os.cpu_count() or 4) - 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/load/score.py", description="Time the SPEC 12 batch scoring target."
    )
    scale_argument(parser)
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=None,
        help=f"override the wall-clock gate (default {SCORE_BUDGET_SECONDS:.0f} s at full size, "
        "scaled with a floor below it)",
    )
    parser.add_argument(
        "--profile-limit", type=int, default=0, help="score at most this many profiles (debugging)"
    )
    database_arguments(parser)
    return parser


def budget_for(nominal_pairs: int, override: float | None) -> tuple[float, str]:
    if override is not None:
        return override, "explicit --budget-seconds"
    fraction = nominal_pairs / FULL_PAIRS if FULL_PAIRS else 1.0
    if fraction >= 0.9:
        return SCORE_BUDGET_SECONDS, "SPEC 12: 50k x 200 in under 10 minutes"
    scaled_budget = max(MIN_SCALED_BUDGET_SECONDS, SCORE_BUDGET_SECONDS * fraction)
    return scaled_budget, (
        f"scaled guard for {fraction:.1%} of the SPEC size "
        f"(floor {MIN_SCALED_BUDGET_SECONDS:.0f} s; the SPEC gate is "
        f"{SCORE_BUDGET_SECONDS:.0f} s at 50k x 200)"
    )


async def corpus(
    args: argparse.Namespace,
) -> tuple[Any, list[tuple[str, str]], int, dict[str, float]]:
    from app.models import Opportunity
    from app.services.matching.engine import candidate_profiles
    from sqlalchemy import func, select

    settings = build_settings(args.database_url, args.owner_database_url)
    database = build_database(settings)
    watch = Stopwatch()
    try:
        with watch.stage("discover_profiles"):
            candidates = await candidate_profiles(database)
        with watch.stage("count_corpus"):
            async with database.owner_session() as session:
                notices = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(Opportunity)
                            .where(Opportunity.duplicate_of.is_(None))
                        )
                    ).scalar_one()
                )
    finally:
        await database.dispose()
    profiles = [(str(c.tenant_id), str(c.profile_id)) for c in candidates]
    if args.profile_limit:
        profiles = profiles[: args.profile_limit]
    return settings, profiles, notices, watch.as_dict()


async def analyze_matches(args: argparse.Namespace) -> None:
    from sqlalchemy import text

    settings = build_settings(args.database_url, args.owner_database_url)
    database = build_database(settings)
    try:
        async with database.owner_engine.begin() as conn:
            await conn.execute(text("ANALYZE matches"))
    finally:
        await database.dispose()


def run_pool(payloads: list[dict[str, Any]], workers: int) -> list[dict[str, Any]]:
    """One worker runs inline so a 1-worker run (and the unit tests) forks nothing."""
    if workers <= 1 or len(payloads) == 1:
        return [score_shard(payload) for payload in payloads]
    context = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=len(payloads), mp_context=context) as pool:
        return list(pool.map(score_shard, payloads))


def run(args: argparse.Namespace) -> dict[str, Any]:
    watch = Stopwatch()
    _settings, profiles, notices, discovery = asyncio.run(corpus(args))
    for name, seconds in discovery.items():
        watch.record(name, seconds)
    if not profiles:
        raise SystemExit("no matchable profile in the database; run scripts/load/seed.py first")
    nominal_pairs = len(profiles) * notices
    budget, why = budget_for(nominal_pairs, args.budget_seconds)
    groups = shards(profiles, max(1, args.workers))
    emit(
        f"scoring {len(profiles)} profiles x {notices:,} notices = {nominal_pairs:,} nominal "
        f"pairs over {len(groups)} worker(s); budget {budget:.0f} s ({why})"
    )
    payloads = [
        {
            "worker": index,
            "app_url": args.database_url,
            "owner_url": args.owner_database_url,
            "page_size": args.page_size,
            "profiles": group,
        }
        for index, group in enumerate(groups)
    ]
    with watch.stage("score"):
        results = run_pool(payloads, len(groups))
    with watch.stage("analyze"):
        asyncio.run(analyze_matches(args))
    totals = Totals()
    errors: list[str] = []
    for result in results:
        totals.add(result)
        errors.extend(result.get("errors", []))
    score_seconds = watch.stages.get("score", 0.0)
    wall = watch.total
    report: dict[str, Any] = {
        "script": "score",
        "database": database_name(args.database_url),
        "scale": args.scale,
        "workers": len(groups),
        "page_size": args.page_size,
        "profiles": len(profiles),
        "opportunities": notices,
        "nominal_pairs": nominal_pairs,
        "stages_seconds": watch.as_dict(),
        "score_seconds": round(score_seconds, 2),
        "wall_seconds": round(wall, 2),
        "nominal_pairs_per_second": round(nominal_pairs / max(score_seconds, 0.001)),
        "scored_pairs_per_second": round(totals.pairs_scored / max(score_seconds, 0.001)),
        "prefilter_survival": round(totals.pairs_scored / max(nominal_pairs, 1), 4),
        "budget_seconds": budget,
        "budget_reason": why,
        "extrapolated_full_scale_seconds": round(wall * (FULL_PAIRS / max(nominal_pairs, 1)), 1),
        "errors": errors,
        **totals.as_dict(),
    }
    report["ok"] = wall <= budget and not errors
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    quiet_logging()
    if args.scale <= 0:
        sys.stderr.write("--scale must be > 0\n")
        return 2
    report = run(args)
    write_report(args.report, report)
    if report["errors"]:
        sys.stderr.write(f"{len(report['errors'])} profile(s) failed to score\n")
    if not report["ok"]:
        sys.stderr.write(
            f"FAIL: scored in {report['wall_seconds']}s, budget {report['budget_seconds']}s "
            f"({report['budget_reason']})\n"
        )
        return 1
    emit(
        f"PASS: {report['nominal_pairs']:,} nominal pairs in {report['wall_seconds']}s "
        f"(budget {report['budget_seconds']:.0f}s, "
        f"{report['nominal_pairs_per_second']:,} nominal pairs/s, "
        f"{report['scored_pairs_per_second']:,} scored pairs/s)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by tests through main()
    raise SystemExit(main())
