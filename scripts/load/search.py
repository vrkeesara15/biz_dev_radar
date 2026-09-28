#!/usr/bin/env python3
"""M7-10 / SPEC 12: measure `GET /api/v1/opportunities` latency over the load corpus.

    python scripts/load/search.py                       # 500 randomized queries, p95 < 500 ms
    python scripts/load/search.py --explain             # EXPLAIN ANALYZE the slowest one
    python scripts/load/search.py --base-url http://localhost:8000   # against a live server

By default the API is driven in-process through `httpx.ASGITransport`, so the number is
"FastAPI route + SQLAlchemy + Postgres" with no network or TLS in it — the part of the
budget the code owns. `--base-url` runs the same queries against a real server when you
want the rest.

The queries are drawn from the same 12-domain vocabulary `seed.py` writes the corpus
from, and mix websearch `q` terms (including phrases, `-term` and `OR`), region, notice
type, NAICS, `due_before`, `status`, `min_score` and paging, so the FTS GIN index, the
NAICS GIN index, the deadline ordering and the matches EXISTS all get exercised.

Exit codes: 0 p95 inside the budget · 1 p95 over it (or requests failed) · 2 bad usage.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from typing import Any

from loadlib import (
    SEARCH_P95_BUDGET_MS,
    Stopwatch,
    build_database,
    build_settings,
    database_arguments,
    database_name,
    emit,
    percentile,
    progress,
    quiet_logging,
    search_terms,
    write_report,
)

DEFAULT_QUERIES = 500
DEFAULT_WARMUP = 10
PAGE_SIZES = (25, 25, 50, 100)
STATUS_SETS = ("open", "open,closing_soon", "closing_soon")
TYPE_SETS = ("rfp", "rfp,combined", "rfq", "gem_bid", "gem_bid,reverse_auction", "sources_sought")
NAICS_SETS = ("541511", "541512,518210", "561210", "237310", "221114,237130")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/load/search.py", description="Measure SPEC 12 search latency."
    )
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument(
        "--p95-ms", type=float, default=SEARCH_P95_BUDGET_MS, help="the gate (SPEC 12: 500 ms)"
    )
    parser.add_argument(
        "--base-url", default=None, help="hit a live server instead of the in-process ASGI app"
    )
    parser.add_argument(
        "--explain", action="store_true", help="print EXPLAIN ANALYZE for the slowest query"
    )
    database_arguments(parser)
    return parser


# --- query generation -------------------------------------------------------------------------


def make_queries(count: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed * 17 + 3)
    terms = search_terms()
    out: list[dict[str, Any]] = []
    for _ in range(count):
        params: dict[str, Any] = {"page_size": rng.choice(PAGE_SIZES)}
        roll = rng.random()
        if roll < 0.45:
            params["q"] = rng.choice(terms)
        elif roll < 0.55:
            params["q"] = f'"{rng.choice(terms)}"'
        elif roll < 0.62:
            params["q"] = f"{rng.choice(terms)} OR {rng.choice(terms)}"
        elif roll < 0.68:
            params["q"] = f"{rng.choice(terms)} -{rng.choice(terms).split()[0]}"
        if rng.random() < 0.5:
            params["region"] = rng.choice(("us", "in"))
        if rng.random() < 0.3:
            params["type"] = rng.choice(TYPE_SETS)
        if rng.random() < 0.25:
            params["naics"] = rng.choice(NAICS_SETS)
        if rng.random() < 0.25:
            params["status"] = rng.choice(STATUS_SETS)
        if rng.random() < 0.2:
            params["min_score"] = rng.choice((50, 60, 70))
        if rng.random() < 0.2:
            params["page"] = rng.randrange(2, 8)
        out.append(params)
    return out


# --- the caller -------------------------------------------------------------------------------


async def pick_principal(database: Any) -> tuple[Any, Any, str]:
    """Any tenant owner: opportunities are global, but the route needs a tenant role."""
    from app.models import Membership, User
    from sqlalchemy import select

    async with database.owner_session() as session:
        row = (
            await session.execute(
                select(Membership.tenant_id, Membership.user_id, User.email)
                .join(User, User.id == Membership.user_id)
                .order_by(Membership.created_at, Membership.id)
                .limit(1)
            )
        ).first()
    if row is None:
        raise SystemExit("no tenant in the database; run scripts/load/seed.py first")
    return row[0], row[1], row[2]


def make_client(args: argparse.Namespace, settings: Any) -> Any:
    import httpx

    if args.base_url:
        return httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=30.0)
    from app.main import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://load.test", timeout=30.0)


async def measure(args: argparse.Namespace, database: Any) -> dict[str, Any]:
    from app.core.auth import encode_token
    from app.core.roles import Role

    settings = build_settings(args.database_url, args.owner_database_url)
    tenant_id, user_id, email = await pick_principal(database)
    token = encode_token(
        user_id=user_id,
        email=email,
        tenant_id=tenant_id,
        role=Role.TENANT_OWNER,
        secret=settings.auth_secret,
        expires_in=7200,
    )
    headers = {"Authorization": f"Bearer {token}"}
    queries = make_queries(args.queries, args.seed)
    warmup = make_queries(max(0, args.warmup), args.seed + 1)
    samples: list[float] = []
    # split by whether the query asks for the matches EXISTS: at 50k x 200 that join is
    # the whole difference between a 30 ms search and a 500 ms one (OQ-106)
    by_kind: dict[str, list[float]] = {"plain": [], "min_score": []}
    failures: list[dict[str, Any]] = []
    total_items = 0
    slowest: tuple[float, dict[str, Any]] | None = None
    client = make_client(args, settings)
    try:
        for params in warmup:
            await client.get("/api/v1/opportunities", params=params, headers=headers)
        for index, params in enumerate(queries, start=1):
            started = time.perf_counter()
            response = await client.get("/api/v1/opportunities", params=params, headers=headers)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if response.status_code != 200:
                failures.append({"params": params, "status": response.status_code})
                continue
            samples.append(elapsed_ms)
            by_kind["min_score" if params.get("min_score") else "plain"].append(elapsed_ms)
            total_items += len(response.json().get("items", []))
            if slowest is None or elapsed_ms > slowest[0]:
                slowest = (elapsed_ms, params)
            if index % 25 == 0 or index == len(queries):
                progress(f"queries {index}/{len(queries)} p95 {percentile(samples, 95):.0f} ms")
    finally:
        await client.aclose()
    emit()
    return {
        "samples": samples,
        "by_kind": by_kind,
        "failures": failures,
        "items_returned": total_items,
        "slowest": slowest,
        "tenant_id": str(tenant_id),
    }


async def explain_slowest(database: Any, tenant_id: Any, params: dict[str, Any]) -> str:
    """Re-plan the slowest query's SELECT with EXPLAIN (ANALYZE, BUFFERS)."""
    from app.api.v1.opportunities import _parse_enums, _split_csv, search_statement
    from app.core.config import Region
    from app.core.opportunity import NoticeType, OpportunityStatus
    from app.services.matching.read import with_min_score
    from sqlalchemy import text
    from sqlalchemy.dialects import postgresql

    stmt = search_statement(
        q=params.get("q"),
        region=Region(params["region"]) if params.get("region") else None,
        notice_types=_parse_enums(params.get("type"), NoticeType, "notice type"),
        naics=_split_csv(params.get("naics")),
        due_before=None,
        statuses=_parse_enums(params.get("status"), OpportunityStatus, "status"),
        include_duplicates=False,
    )
    stmt = with_min_score(stmt, params.get("min_score"))
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 25))
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    async with database.session(tenant_id) as session:
        rows = (await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {compiled}"))).all()
    return "\n".join(str(row[0]) for row in rows)


async def run_async(args: argparse.Namespace, database: Any = None) -> dict[str, Any]:
    """One event loop for the whole run: asyncpg connections are loop-bound."""
    from app.core.db import set_database

    settings = build_settings(args.database_url, args.owner_database_url)
    owned = database is None
    database = database or build_database(settings)
    # the FastAPI dependency resolves the process-wide Database, so install ours
    set_database(database)
    watch = Stopwatch()
    emit(
        f"running {args.queries} randomized searches against "
        f"{args.base_url or 'the in-process ASGI app'} "
        f"({database_name(args.database_url)}); budget p95 {args.p95_ms:.0f} ms"
    )
    explain_text = ""
    try:
        with watch.stage("requests"):
            outcome = await measure(args, database)
        if args.explain and outcome["slowest"] is not None:
            with watch.stage("explain"):
                try:
                    explain_text = await explain_slowest(
                        database, outcome["tenant_id"], outcome["slowest"][1]
                    )
                except Exception as exc:  # EXPLAIN is a diagnostic, never the gate
                    explain_text = f"EXPLAIN failed: {type(exc).__name__}: {exc}"
    finally:
        if owned:
            await database.dispose()
    samples = outcome["samples"]
    slowest = outcome["slowest"]
    report: dict[str, Any] = {
        "script": "search",
        "database": database_name(args.database_url),
        "target": args.base_url or "asgi",
        "queries": args.queries,
        "ok_responses": len(samples),
        "failures": outcome["failures"],
        "items_returned": outcome["items_returned"],
        "p50_ms": round(percentile(samples, 50), 1),
        "p95_ms": round(percentile(samples, 95), 1),
        "p99_ms": round(percentile(samples, 99), 1),
        "max_ms": round(max(samples), 1) if samples else 0.0,
        "mean_ms": round(sum(samples) / len(samples), 1) if samples else 0.0,
        "budget_p95_ms": args.p95_ms,
        "slowest_query": None if slowest is None else slowest[1],
        "slowest_ms": 0.0 if slowest is None else round(slowest[0], 1),
        "by_kind": {
            kind: {
                "queries": len(values),
                "p50_ms": round(percentile(values, 50), 1),
                "p95_ms": round(percentile(values, 95), 1),
                "p99_ms": round(percentile(values, 99), 1),
            }
            for kind, values in outcome["by_kind"].items()
        },
        "stages_seconds": watch.as_dict(),
        "total_seconds": watch.total,
    }
    report["ok"] = bool(samples) and report["p95_ms"] <= args.p95_ms and not outcome["failures"]
    if explain_text:
        report["explain"] = explain_text
    return report


def run(args: argparse.Namespace, database: Any = None) -> dict[str, Any]:
    return asyncio.run(run_async(args, database))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    quiet_logging()
    if args.queries <= 0:
        sys.stderr.write("--queries must be > 0\n")
        return 2
    report = run(args)
    explain_text = report.pop("explain", "")
    write_report(args.report, report)
    if explain_text:
        emit(
            "\nEXPLAIN (ANALYZE, BUFFERS) of the slowest query "
            f"({report['slowest_ms']} ms, {report['slowest_query']}):"
        )
        emit(explain_text)
    if not report["ok"]:
        sys.stderr.write(
            f"FAIL: p95 {report['p95_ms']} ms against a {args.p95_ms:.0f} ms budget"
            + (f", {len(report['failures'])} failed requests" if report["failures"] else "")
            + "\n"
        )
        return 1
    emit(
        f"PASS: p50 {report['p50_ms']} ms · p95 {report['p95_ms']} ms · "
        f"p99 {report['p99_ms']} ms over {report['ok_responses']} queries"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by tests through main()
    raise SystemExit(main())
