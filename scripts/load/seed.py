#!/usr/bin/env python3
"""M7-10 / SPEC 12: fill a disposable database with the load corpus.

    python scripts/load/seed.py --reset                    # 50,000 notices, 200 profiles
    python scripts/load/seed.py --scale 0.1 --reset        # 5,000 x 20 (the CI smoke)
    python scripts/load/seed.py --profiles 40 --opportunities 2000 --reset

What it writes
  * `--tenants` tenants (default 20), half `us` and half `in`, each with an owner user;
  * `--profiles` company profiles (default 200) spread evenly over them, every one of
    them complete enough to be matched (completeness >= 40: identity, registrations,
    3 FY revenue, >= 3 codes, 5 include + 1 exclude keyword, one service line, one past
    performance, a value range, wanted notice types and target geography);
  * the profile knowledge base (`kb_chunks`) for each profile, embedded with
    `FakeEmbeddings`, so the semantic and past-performance signals do real vector work
    without a provider key;
  * `--opportunities` notices (default 50,000) with realistic distributions — notice
    type and status weighted per region, NAICS drawn 70% from the profiles' own codes
    and 30% from unrelated ones, deadlines spread over 90 days with 10% already past and
    8% unpublished, titles and descriptions from a 12-domain vocabulary — each carrying
    a `FakeEmbeddings` vector so `opportunities.embedding` is never NULL at score time.

Everything is deterministic in `--seed`: ids are uuid5(seed, kind, index), so re-seeding
after `--reset` reproduces the same corpus.

Exit codes: 0 seeded · 1 the corpus is unusable (no matchable profile) · 2 bad usage.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from loadlib import (
    DOMAIN_BY_KEY,
    FULL_OPPORTUNITIES,
    FULL_PROFILES,
    FULL_TENANTS,
    OpportunitySpec,
    ProfileSpec,
    Stopwatch,
    build_database,
    build_settings,
    database_arguments,
    database_name,
    emit,
    generate_opportunities,
    generate_profiles,
    is_disposable,
    iter_specs,
    offset_days,
    progress,
    quiet_logging,
    scale_argument,
    scaled,
    utcnow,
    write_report,
)

DEFAULT_BATCH = 5_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/load/seed.py", description="Seed the SPEC 12 load corpus."
    )
    parser.add_argument("--profiles", type=int, default=FULL_PROFILES)
    parser.add_argument("--opportunities", type=int, default=FULL_OPPORTUNITIES)
    parser.add_argument("--tenants", type=int, default=FULL_TENANTS)
    scale_argument(parser)
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH, help="rows per executemany round trip"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="TRUNCATE every application table first (required to re-seed)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow --reset against a database whose name says nothing about being disposable",
    )
    parser.add_argument(
        "--no-analyze", action="store_true", help="skip the ANALYZE after the bulk insert"
    )
    database_arguments(parser)
    return parser


def resolve_sizes(args: argparse.Namespace) -> tuple[int, int, int]:
    return (
        scaled(args.profiles, args.scale),
        scaled(args.opportunities, args.scale),
        scaled(args.tenants, args.scale),
    )


# --- writers ---------------------------------------------------------------------------------


async def truncate(database: Any) -> list[str]:
    from app.models.base import Base
    from sqlalchemy import text

    keep = {"plan_limits"}  # reference data the migrations seed
    names = [t.name for t in Base.metadata.sorted_tables if t.name not in keep]
    async with database.owner_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"
            )
        )
    return names


async def existing_counts(database: Any) -> dict[str, int]:
    from app.models import CompanyProfile, Opportunity, Tenant
    from sqlalchemy import func, select

    async with database.owner_session() as session:
        out = {}
        for label, model in (
            ("tenants", Tenant),
            ("profiles", CompanyProfile),
            ("opportunities", Opportunity),
        ):
            out[label] = int(
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
            )
        return out


async def write_tenants(database: Any, specs: list[ProfileSpec]) -> int:
    from app.core.config import Region
    from app.core.plan import Plan
    from app.core.roles import Role
    from app.models import Membership, Tenant, User

    seen: dict[int, ProfileSpec] = {}
    for spec in specs:
        seen.setdefault(spec.tenant_index, spec)
    async with database.owner_session() as session:
        for spec in seen.values():
            session.add(
                Tenant(
                    id=spec.tenant_id,
                    name=f"Load tenant {spec.tenant_index:03d} ({spec.region})",
                    slug=spec.slug,
                    region=Region(spec.region),
                    data_residency=Region(spec.region),
                    plan=Plan.PRO,
                    is_internal=False,
                )
            )
        await session.flush()
        for spec in specs:
            session.add(User(id=spec.user_id, email=spec.email, name=f"Owner {spec.index:04d}"))
        await session.flush()
        for spec in specs:
            session.add(
                Membership(tenant_id=spec.tenant_id, user_id=spec.user_id, role=Role.TENANT_OWNER)
            )
        await session.flush()
    return len(seen)


def profile_kwargs(spec: ProfileSpec) -> dict[str, Any]:
    from decimal import Decimal

    from app.core.config import Region
    from app.core.profile_fields import LegalStructure

    domain = DOMAIN_BY_KEY[spec.domain_key]
    common: dict[str, Any] = {
        "id": spec.profile_id,
        "tenant_id": spec.tenant_id,
        "region": Region(spec.region),
        "is_active": True,
        "version": 1,
        "legal_name": spec.legal_name,
        "addresses": [
            {
                "kind": "hq",
                "line1": f"{100 + spec.index} Load Street",
                "city": spec.states[0],
                "state": spec.states[0],
                "postal_code": "12345",
                "country": "US" if spec.region == "us" else "IN",
            }
        ],
        "website": f"https://load-{spec.index:04d}.example",
        "phone": "+1-555-0100" if spec.region == "us" else "+91-22-5550100",
        "bid_inbox_email": f"bids-{spec.index:04d}@load.example",
        "year_founded": 1995 + (spec.index % 25),
        "legal_structure": LegalStructure.LLC if spec.region == "us" else LegalStructure.PVT_LTD,
        "employee_count_total": 25 + (spec.index % 400),
        "annual_revenue": [
            {
                "fiscal_year": year,
                "amount": f"{(4 + spec.index % 9) * 1_000_000}.00",
                "currency": "USD" if spec.region == "us" else "INR",
            }
            for year in (2023, 2024, 2025)
        ],
        "audited_fiscal_years": [2023, 2024, 2025],
        "bonding_capacity_amount": Decimal("5000000.00"),
        "bonding_capacity_currency": "USD" if spec.region == "us" else "INR",
        "target_countries": ["US"] if spec.region == "us" else ["IN"],
        "remote_ok": spec.index % 3 == 0,
        "notice_types_wanted": list(spec.wanted_types),
        "target_buyers": [spec.buyer],
        "contract_types_preferred": ["ffp"],
        "teaming_roles": ["prime", "sub"],
        "cleared_personnel_count": 4,
        "output_languages": ["en"] if spec.region == "us" else ["en", "hi"],
    }
    if spec.region == "us":
        common.update(
            {
                "uei": f"UEI{spec.index:09d}"[:12],
                "cage_code": f"{spec.index % 100000:05d}",
                "ein": f"12-{spec.index % 10000000:07d}",
                "target_us_states": list(spec.states),
                "value_min_usd": Decimal("50000.00"),
                "value_max_usd": Decimal("25000000.00"),
            }
        )
    else:
        common.update(
            {
                "pan": f"AAAAA{spec.index % 10000:04d}A",
                "gstin": f"27AAAAA{spec.index % 10000:04d}A1Z5",
                "cin_llpin": f"U72900MH20{spec.index % 100:02d}PTC{spec.index % 1000000:06d}",
                "udyam_number": f"UDYAM-MH-01-{spec.index % 10000000:07d}",
                "gem_seller_id": f"GEMS{spec.index:07d}",
                "target_in_states": list(spec.states),
                "value_min_inr": Decimal("2000000.00"),
                "value_max_inr": Decimal("900000000.00"),
            }
        )
    common["_domain"] = domain
    return common


async def write_profiles(database: Any, specs: list[ProfileSpec], *, batch_size: int) -> int:
    from app.core.profile_fields import CodeScheme, KeywordKind, PerformanceRole
    from app.models import (
        CompanyProfile,
        PastPerformance,
        ProfileCode,
        ProfileKeyword,
        ServiceLine,
    )

    written = 0
    async with database.owner_session() as session:
        for batch in iter_specs(specs, max(1, min(batch_size, 200))):
            for spec in batch:
                values = profile_kwargs(spec)
                domain = values.pop("_domain")
                session.add(CompanyProfile(**values))
                scheme = CodeScheme.NAICS if spec.region == "us" else CodeScheme.INDIA_CATEGORY
                for position, code in enumerate(spec.codes):
                    session.add(
                        ProfileCode(
                            tenant_id=spec.tenant_id,
                            profile_id=spec.profile_id,
                            scheme=scheme,
                            code=code,
                            is_primary=position == 0,
                        )
                    )
                for term in spec.keywords:
                    session.add(
                        ProfileKeyword(
                            tenant_id=spec.tenant_id,
                            profile_id=spec.profile_id,
                            kind=KeywordKind.INCLUDE,
                            term=term,
                        )
                    )
                session.add(
                    ProfileKeyword(
                        tenant_id=spec.tenant_id,
                        profile_id=spec.profile_id,
                        kind=KeywordKind.EXCLUDE,
                        term=spec.exclude_keyword,
                    )
                )
                session.add(
                    ServiceLine(
                        tenant_id=spec.tenant_id,
                        profile_id=spec.profile_id,
                        name=domain.name,
                        description=domain.body,
                    )
                )
                session.add(
                    PastPerformance(
                        tenant_id=spec.tenant_id,
                        profile_id=spec.profile_id,
                        title=f"{domain.nouns[0].title()} for {spec.buyer}",
                        customer=spec.buyer,
                        role=PerformanceRole.PRIME,
                        scope=domain.body,
                    )
                )
                written += 1
            await session.flush()
            progress(f"profiles {written}/{len(specs)}")
    emit()
    return written


async def index_knowledge_base(database: Any, settings: Any, specs: list[ProfileSpec]) -> int:
    from app.services.embeddings import FakeEmbeddings
    from app.services.knowledge_base import index_profile
    from app.services.storage import StorageRouter

    embeddings = FakeEmbeddings(settings.embedding_dim)
    storage = StorageRouter(settings)
    by_tenant: dict[Any, list[ProfileSpec]] = {}
    for spec in specs:
        by_tenant.setdefault(spec.tenant_id, []).append(spec)
    chunks = 0
    done = 0
    for tenant_id, tenant_specs in by_tenant.items():
        async with database.session(tenant_id) as session:
            for spec in tenant_specs:
                result = await index_profile(
                    session, spec.profile_id, embeddings=embeddings, storage=storage
                )
                chunks += result.chunks
                done += 1
                progress(f"knowledge base {done}/{len(specs)} profiles, {chunks} chunks")
    emit()
    return chunks


def opportunity_row(spec: OpportunitySpec, now: Any, embeddings: Any) -> dict[str, Any]:
    from decimal import Decimal

    from app.core.config import Region
    from app.core.opportunity import NoticeType, OpportunityStatus

    domain = DOMAIN_BY_KEY[spec.domain_key]
    rate = Decimal("1") if spec.currency == "USD" else Decimal("0.012")
    value_min = Decimal(str(spec.value_min))
    value_max = Decimal(str(spec.value_max))
    text = f"{spec.title}\n\n{spec.description}"
    return {
        "id": spec.opportunity_id,
        "source_id": spec.source_id,
        "external_id": spec.external_id,
        "source_url": f"https://load.example/{spec.source_id}/{spec.external_id}",
        "region": Region(spec.region),
        "country": spec.country,
        "currency": spec.currency,
        "notice_type": NoticeType(spec.notice_type),
        "title": spec.title,
        "description_text": spec.description,
        "summary_ai": None,
        "solicitation_number": f"LOAD-{spec.index:07d}",
        "buyer_org": spec.buyer,
        "buyer_hierarchy": [spec.buyer],
        "naics": list(spec.naics),
        "psc": [],
        "aln": [],
        "india_category": [domain.gem_category] if spec.region == "in" else [],
        "set_aside": spec.set_aside,
        "reservation": spec.reservation,
        "place_of_performance": {
            "country": spec.country,
            "state": spec.state,
            "city": spec.state,
        },
        "estimated_value_min": value_min,
        "estimated_value_max": value_max,
        "estimated_value_min_usd": (value_min * rate).quantize(Decimal("0.01")),
        "estimated_value_max_usd": (value_max * rate).quantize(Decimal("0.01")),
        "posted_at": offset_days(now, spec.posted_offset_days),
        "response_due_at": offset_days(now, spec.due_offset_days),
        "source_tz": "America/New_York" if spec.region == "us" else "Asia/Kolkata",
        "status": OpportunityStatus(spec.status),
        "detail_status": "full",
        "content_hash": f"{spec.opportunity_id.hex}",
        "version": 1,
        "duplicate_of": None,
        "reference_norm": f"load{spec.index:07d}",
        "buyer_norm": spec.buyer.lower(),
        "embedding": embeddings.vector(text),
        "extra": {"load": True, "domain": spec.domain_key},
        "last_seen_at": now,
    }


async def write_opportunities(
    database: Any, settings: Any, *, count: int, seed: int, batch_size: int
) -> int:
    from app.models import Opportunity
    from app.services.embeddings import FakeEmbeddings
    from sqlalchemy import insert

    embeddings = FakeEmbeddings(settings.embedding_dim)
    now = utcnow()
    written = 0
    started = Stopwatch()
    async with database.owner_session() as session:
        for batch in iter_specs(generate_opportunities(count=count, seed=seed), batch_size):
            rows = [opportunity_row(spec, now, embeddings) for spec in batch]
            await session.execute(insert(Opportunity), rows)
            written += len(rows)
            rate = written / max(started.total, 0.001)
            progress(f"opportunities {written}/{count} ({rate:,.0f} rows/s)")
    emit()
    return written


async def analyze(database: Any) -> None:
    from sqlalchemy import text

    async with database.owner_engine.begin() as conn:
        for table in ("opportunities", "kb_chunks", "company_profiles", "matches"):
            await conn.execute(text(f"ANALYZE {table}"))


async def matchable_profiles(database: Any) -> int:
    from app.services.matching.engine import candidate_profiles

    return len(await candidate_profiles(database))


# --- entry point -----------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> dict[str, Any]:
    profiles, opportunities, tenants = resolve_sizes(args)
    settings = build_settings(args.database_url, args.owner_database_url)
    database = build_database(settings)
    watch = Stopwatch()
    emit(
        f"seeding {opportunities:,} opportunities and {profiles:,} profiles across "
        f"{tenants} tenants into {database_name(args.database_url)} (seed {args.seed})"
    )
    try:
        if args.reset:
            with watch.stage("truncate"):
                tables = await truncate(database)
            emit(f"truncated {len(tables)} tables")
        else:
            counts = await existing_counts(database)
            if any(counts.values()):
                raise SystemExit(
                    f"database is not empty ({counts}); re-run with --reset to replace it"
                )
        specs = generate_profiles(profiles=profiles, tenants=tenants, seed=args.seed)
        with watch.stage("tenants"):
            tenant_count = await write_tenants(database, specs)
        with watch.stage("profiles"):
            await write_profiles(database, specs, batch_size=args.batch_size)
        with watch.stage("knowledge_base"):
            chunks = await index_knowledge_base(database, settings, specs)
        with watch.stage("opportunities"):
            written = await write_opportunities(
                database,
                settings,
                count=opportunities,
                seed=args.seed,
                batch_size=max(1, args.batch_size),
            )
        if not args.no_analyze:
            with watch.stage("analyze"):
                await analyze(database)
        with watch.stage("verify"):
            matchable = await matchable_profiles(database)
    finally:
        await database.dispose()
    report = {
        "script": "seed",
        "database": database_name(args.database_url),
        "seed": args.seed,
        "scale": args.scale,
        "tenants": tenant_count,
        "profiles": len(specs),
        "matchable_profiles": matchable,
        "kb_chunks": chunks,
        "opportunities": written,
        "batch_size": args.batch_size,
        "stages_seconds": watch.as_dict(),
        "total_seconds": watch.total,
        "rows_per_second": round(written / max(watch.stages.get("opportunities", 0.001), 0.001)),
        "ok": matchable == len(specs),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    quiet_logging()
    if args.reset and not (is_disposable(args.database_url) or args.force):
        sys.stderr.write(
            f"refusing to TRUNCATE {database_name(args.database_url)!r}: the name says nothing "
            "about being disposable. Point --database-url at bidradar_load or pass --force.\n"
        )
        return 2
    if args.scale <= 0:
        sys.stderr.write("--scale must be > 0\n")
        return 2
    report = asyncio.run(run(args))
    write_report(args.report, report)
    if not report["ok"]:
        sys.stderr.write(
            f"only {report['matchable_profiles']}/{report['profiles']} profiles clear the "
            "matching completeness threshold; the corpus would under-report the load\n"
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by tests through main()
    raise SystemExit(main())
