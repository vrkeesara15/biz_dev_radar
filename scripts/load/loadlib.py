"""Shared plumbing for the SPEC 12 load scripts (M7-10).

The three entry points (`seed.py`, `score.py`, `search.py`) live next to this module and
import it directly, so they must be run as files:

    uv run python ../scripts/load/seed.py --help      # from backend/, or
    python scripts/load/seed.py --help                # from the repo root

Importing this module puts `backend/` on `sys.path`; every `app.*` import in these
scripts is therefore made INSIDE a function, after that has happened. Nothing here
touches the process-wide `Settings` / `Database` / embedding provider singletons unless
a script asks for it explicitly (`search.py` has to, because the FastAPI app resolves
its database through `app.core.db.get_database`).

Determinism: every generator takes a `random.Random(seed)` and every row id is a
`uuid5` of (seed, kind, index), so the same `--seed` produces byte-identical rows and a
re-seed after `--reset` reuses the same ids.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:  # before any `app.*` import in this process
    sys.path.insert(0, str(BACKEND_ROOT))

# SPEC 12: "Load: 50k opportunities, 200 profiles scored in < 10 min; search p95 < 500 ms"
FULL_OPPORTUNITIES = 50_000
FULL_PROFILES = 200
FULL_TENANTS = 20
SCORE_BUDGET_SECONDS = 600.0
SEARCH_P95_BUDGET_MS = 500.0
DEFAULT_SEED = 20260927

# a scaled run is a smoke test, not the SPEC gate: its wall-clock guard never drops below
# this, because a 6-second budget on a shared CI runner only measures the runner.
MIN_SCALED_BUDGET_SECONDS = 180.0

DEFAULT_DATABASE_URL = "postgresql+asyncpg://bidradar_app:bidradar_app@localhost:5433/bidradar_load"
DEFAULT_OWNER_URL = "postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar_load"

# --reset truncates. Refuse to do that to a database that is not obviously disposable.
DISPOSABLE_NAMES = ("load", "test")

NAMESPACE = uuid.UUID("8f0a9a1e-0d3b-5f8a-9a1e-0d3b5f8a9a1e")


def emit(line: str = "") -> None:
    """stdout without `print` (ruff T20 is on for this repo)."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def progress(line: str) -> None:
    """One rewritten line; the caller ends with `emit()` to keep the final state."""
    sys.stdout.write("\r" + line.ljust(78))
    sys.stdout.flush()


def deterministic_id(kind: str, seed: int, index: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{seed}:{index}")


def scaled(value: int, scale: float, *, minimum: int = 1) -> int:
    return max(minimum, round(value * scale))


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation: with 500 samples it is the honest one)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


def shards(items: Sequence[Any], workers: int) -> list[list[Any]]:
    """Round-robin so a slow profile does not land every heavy neighbour in one shard."""
    count = max(1, min(workers, len(items) or 1))
    out: list[list[Any]] = [[] for _ in range(count)]
    for index, item in enumerate(items):
        out[index % count].append(item)
    return [s for s in out if s]


def chunked(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# --- argument plumbing ----------------------------------------------------------------------


def database_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="app-role URL (RLS applies); default $DATABASE_URL or the compose bidradar_load",
    )
    parser.add_argument(
        "--owner-database-url",
        default=os.environ.get("DATABASE_URL_OWNER", DEFAULT_OWNER_URL),
        help="owner-role URL (migrations, cross-tenant reads); default $DATABASE_URL_OWNER",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="generator seed")
    parser.add_argument(
        "--report", default=None, help="write the run's JSON report to this path as well"
    )


def scale_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="fraction of the SPEC 12 size (0.1 = 5k notices x 20 profiles, the CI smoke)",
    )


def database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def is_disposable(url: str) -> bool:
    name = database_name(url).lower()
    return any(token in name for token in DISPOSABLE_NAMES)


def build_settings(app_url: str, owner_url: str) -> Any:
    """Settings for a load run: the given database, fake embeddings, no rate limiter.

    `_env_file=None` so a developer's `backend/.env` cannot silently point the run at the
    dev database, and `rate_limit_enabled=False` because 500 search requests from one
    process would otherwise trip the per-IP bucket (120/min) at request 121.
    """
    from app.core.config import Settings

    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="load",
        database_url=app_url,
        database_url_owner=owner_url,
        embedding_provider="fake",
        celery_task_always_eager=False,
        email_provider="memory",
        storage_backend="local",
        rate_limit_enabled=False,
    )


def build_database(settings: Any) -> Any:
    from app.core.db import Database

    return Database(settings.database_url, settings.database_url_owner)


def quiet_logging(level: str = "WARNING") -> None:
    """A 200-profile run would otherwise print one `kb.indexed` line per profile."""
    from app.logging import configure_logging

    configure_logging(level)


def write_report(path: str | None, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True, default=str)
    emit(body)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body + "\n")
        emit(f"report written to {target}")


class Stopwatch:
    """Named stage timings; `as_dict()` goes straight into the JSON report."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.stages: dict[str, float] = {}

    def stage(self, name: str) -> _Stage:
        return _Stage(self, name)

    def record(self, name: str, seconds: float) -> None:
        self.stages[name] = round(self.stages.get(name, 0.0) + seconds, 3)

    @property
    def total(self) -> float:
        return round(time.perf_counter() - self.started, 3)

    def as_dict(self) -> dict[str, float]:
        return dict(self.stages)


class _Stage:
    def __init__(self, watch: Stopwatch, name: str) -> None:
        self.watch = watch
        self.name = name
        self.started = 0.0

    def __enter__(self) -> _Stage:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.watch.record(self.name, time.perf_counter() - self.started)


# --- the corpus vocabulary ---------------------------------------------------------------------
#
# Twelve domains, six US and six IN. A profile owns one domain (round robin) and a notice
# draws one from a weighted distribution, so the semantic and keyword signals have real
# work to do instead of scoring every pair identically.


@dataclass(frozen=True, slots=True)
class Domain:
    key: str
    name: str
    region: str
    naics: tuple[str, ...]
    gem_category: str
    keywords: tuple[str, ...]
    exclude: str
    nouns: tuple[str, ...]
    body: str


DOMAINS: tuple[Domain, ...] = (
    Domain(
        key="cloud",
        name="Cloud migration and modernization",
        region="us",
        naics=("541511", "541512", "518210"),
        gem_category="cloud services",
        keywords=("cloud migration", "kubernetes", "devops", "mainframe modernization", "iaas"),
        exclude="janitorial",
        nouns=("cloud migration services", "application modernization", "container platform"),
        body=(
            "The contractor shall migrate legacy mainframe workloads to a commercial cloud "
            "environment, standing up kubernetes container orchestration, devops automation "
            "pipelines and infrastructure as code for the government program office."
        ),
    ),
    Domain(
        key="cyber",
        name="Cybersecurity and zero trust",
        region="us",
        naics=("541512", "541519", "561621"),
        gem_category="cyber security",
        keywords=("zero trust", "incident response", "penetration testing", "siem", "rmf"),
        exclude="landscaping",
        nouns=("zero trust architecture", "security operations center", "penetration testing"),
        body=(
            "The contractor shall operate a security operations center, deliver continuous "
            "monitoring under the risk management framework, run penetration testing and "
            "incident response, and implement a zero trust architecture across the enclave."
        ),
    ),
    Domain(
        key="data",
        name="Data engineering and analytics",
        region="us",
        naics=("541511", "518210", "541618"),
        gem_category="data analytics",
        keywords=("data warehouse", "etl pipeline", "dashboards", "data governance", "sql"),
        exclude="catering",
        nouns=("data warehouse modernization", "analytics platform", "reporting dashboards"),
        body=(
            "The contractor shall build and operate an enterprise data warehouse, author etl "
            "pipelines, publish executive dashboards and stand up a data governance program "
            "covering quality, lineage and stewardship."
        ),
    ),
    Domain(
        key="facilities",
        name="Facilities operations and maintenance",
        region="us",
        naics=("561210", "238220", "561730"),
        gem_category="facility management",
        keywords=("hvac maintenance", "grounds", "custodial", "preventive maintenance", "boiler"),
        exclude="software development",
        nouns=("base operations support", "hvac maintenance", "grounds maintenance"),
        body=(
            "The contractor shall provide base operations support including preventive and "
            "corrective hvac maintenance, custodial services, grounds and snow removal across "
            "the installation on a firm fixed price basis."
        ),
    ),
    Domain(
        key="health",
        name="Health IT and clinical staffing",
        region="us",
        naics=("621111", "541511", "621399"),
        gem_category="healthcare services",
        keywords=("ehr", "hl7", "clinical staffing", "telehealth", "hipaa"),
        exclude="construction",
        nouns=("electronic health record support", "telehealth services", "clinical staffing"),
        body=(
            "The contractor shall support the electronic health record, deliver hl7 and fhir "
            "interface engineering, provide telehealth operations and supply credentialed "
            "clinical staffing consistent with hipaa safeguards."
        ),
    ),
    Domain(
        key="logistics",
        name="Logistics and supply chain",
        region="us",
        naics=("488510", "493110", "541614"),
        gem_category="logistics services",
        keywords=("warehousing", "freight", "inventory management", "3pl", "distribution"),
        exclude="legal services",
        nouns=("warehousing and distribution", "freight transportation", "inventory management"),
        body=(
            "The contractor shall provide third party logistics including warehousing, "
            "inventory management, freight transportation and last mile distribution with "
            "barcode traceability and monthly performance reporting."
        ),
    ),
    Domain(
        key="in_it",
        name="IT services and system integration",
        region="in",
        naics=("541511", "541512"),
        gem_category="software development services",
        keywords=("system integration", "web portal", "api development", "aadhaar", "digilocker"),
        exclude="civil works",
        nouns=("e-governance portal", "system integration", "mobile application development"),
        body=(
            "The bidder shall design, develop and maintain an e-governance web portal with api "
            "integration to departmental systems, single sign on, digilocker document flows and "
            "state data centre hosting for the duration of the contract."
        ),
    ),
    Domain(
        key="in_solar",
        name="Solar and renewable energy",
        region="in",
        naics=("221114", "237130"),
        gem_category="solar power plant",
        keywords=("rooftop solar", "photovoltaic", "epc", "net metering", "inverter"),
        exclude="staffing",
        nouns=("rooftop solar plant", "solar epc works", "grid connected photovoltaic system"),
        body=(
            "The bidder shall execute turnkey epc for a grid connected rooftop solar "
            "photovoltaic plant including modules, inverters, net metering, five year "
            "comprehensive operation and maintenance and commissioning approvals."
        ),
    ),
    Domain(
        key="in_civil",
        name="Civil works and infrastructure",
        region="in",
        naics=("237310", "236220"),
        gem_category="civil works",
        keywords=("road construction", "rcc", "bituminous", "drainage", "boq"),
        exclude="software development",
        nouns=("road strengthening works", "building construction", "drainage improvement"),
        body=(
            "The bidder shall carry out civil works comprising earthwork, rcc structures, "
            "bituminous road strengthening and drainage improvement strictly as per the bill "
            "of quantities and the state schedule of rates."
        ),
    ),
    Domain(
        key="in_medical",
        name="Medical equipment supply",
        region="in",
        naics=("339112", "423450"),
        gem_category="medical equipment",
        keywords=("patient monitor", "ventilator", "cdsco", "ultrasound", "biomedical"),
        exclude="road construction",
        nouns=("patient monitoring systems", "ventilator supply", "ultrasound machines"),
        body=(
            "The bidder shall supply, install and commission cdsco registered medical equipment "
            "including patient monitors and ventilators with five year comprehensive warranty, "
            "biomedical training and spares availability."
        ),
    ),
    Domain(
        key="in_manpower",
        name="Manpower and facility services",
        region="in",
        naics=("561320", "561210"),
        gem_category="manpower outsourcing services",
        keywords=("housekeeping", "security guard", "manpower", "epf", "esic"),
        exclude="medical equipment",
        nouns=("housekeeping services", "security manpower", "outsourced staffing"),
        body=(
            "The bidder shall deploy trained housekeeping and security manpower with statutory "
            "epf and esic compliance, uniforms, supervision and police verification for all "
            "deployed personnel."
        ),
    ),
    Domain(
        key="in_water",
        name="Water supply and treatment",
        region="in",
        naics=("221310", "237110"),
        gem_category="water treatment plant",
        keywords=("water treatment", "pipeline", "scada", "pumping station", "jal jeevan"),
        exclude="manpower",
        nouns=("water supply scheme", "water treatment plant", "pipeline laying works"),
        body=(
            "The bidder shall design and build a water treatment plant and distribution "
            "pipeline network with pumping stations, scada instrumentation and ten year "
            "operation and maintenance under the jal jeevan mission."
        ),
    ),
)

DOMAIN_BY_KEY: dict[str, Domain] = {d.key: d for d in DOMAINS}

DOMAINS_BY_REGION: dict[str, tuple[Domain, ...]] = {
    "us": tuple(d for d in DOMAINS if d.region == "us"),
    "in": tuple(d for d in DOMAINS if d.region == "in"),
}

# (notice type, weight) per region; drawn independently of the domain
NOTICE_TYPES: dict[str, tuple[tuple[str, int], ...]] = {
    "us": (
        ("rfp", 28),
        ("rfq", 20),
        ("combined", 16),
        ("sources_sought", 12),
        ("presolicitation", 10),
        ("rfi", 8),
        ("grant", 6),
    ),
    "in": (
        ("gem_bid", 34),
        ("rfq", 18),
        ("reverse_auction", 14),
        ("rfp", 12),
        ("eoi", 10),
        ("corrigendum", 7),
        ("special", 5),
    ),
}

# what a profile is willing to see: a realistic profile wants a few types, not all of them
WANTED_TYPES: dict[str, tuple[tuple[str, ...], ...]] = {
    "us": (
        ("rfp", "combined", "rfq"),
        ("rfp", "sources_sought", "presolicitation"),
        ("rfq", "combined", "rfi"),
        ("rfp", "grant", "combined"),
    ),
    "in": (
        ("gem_bid", "rfq", "reverse_auction"),
        ("gem_bid", "rfp", "eoi"),
        ("rfq", "reverse_auction", "special"),
        ("gem_bid", "corrigendum", "rfp"),
    ),
}

STATUSES: tuple[tuple[str, int], ...] = (
    ("open", 74),
    ("closing_soon", 14),
    ("closed", 7),
    ("cancelled", 3),
    ("awarded", 2),
)

BUYERS: dict[str, tuple[str, ...]] = {
    "us": (
        "Department of Veterans Affairs",
        "Department of the Army",
        "General Services Administration",
        "Department of Homeland Security",
        "Department of Health and Human Services",
        "Department of the Navy",
        "Department of Energy",
        "National Aeronautics and Space Administration",
    ),
    "in": (
        "Ministry of Railways",
        "Central Public Works Department",
        "Municipal Corporation of Greater Mumbai",
        "Bharat Heavy Electricals Limited",
        "National Health Mission",
        "Public Works Department Karnataka",
        "Uttar Pradesh Jal Nigam",
        "Airports Authority of India",
    ),
}

US_STATES = ("VA", "MD", "TX", "CA", "NC", "OH", "GA", "WA", "CO", "FL")
IN_STATES = (
    "Maharashtra",
    "Karnataka",
    "Delhi",
    "Tamil Nadu",
    "Gujarat",
    "Uttar Pradesh",
    "West Bengal",
    "Telangana",
)
SOURCES: dict[str, tuple[str, ...]] = {
    "us": ("sam_opps", "grants_gov", "sam_awards"),
    "in": ("gem", "cppp", "gepnic_mh"),
}
# NAICS that belong to no seeded profile: the "noise" share of the corpus
NOISE_NAICS = ("311812", "451110", "713940", "722511", "812112", "111150")

SET_ASIDES: tuple[str | None, ...] = (None, None, None, None, "sba", "8a", "sdvosb", "wosb")
RESERVATIONS: tuple[str | None, ...] = (None, None, None, "msme", "startup", "make_in_india")


def weighted_choice(rng: Any, table: Sequence[tuple[Any, int]]) -> Any:
    total = sum(weight for _, weight in table)
    pick = rng.randrange(total)
    upto = 0
    for value, weight in table:
        upto += weight
        if pick < upto:
            return value
    return table[-1][0]  # pragma: no cover - unreachable while weights are positive


# --- generated specs ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    index: int
    tenant_index: int
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    profile_id: uuid.UUID
    region: str
    domain_key: str
    legal_name: str
    slug: str
    email: str
    codes: tuple[str, ...]
    keywords: tuple[str, ...]
    exclude_keyword: str
    wanted_types: tuple[str, ...]
    states: tuple[str, ...]
    buyer: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OpportunitySpec:
    index: int
    opportunity_id: uuid.UUID
    source_id: str
    external_id: str
    region: str
    country: str
    currency: str
    notice_type: str
    status: str
    title: str
    description: str
    naics: tuple[str, ...]
    buyer: str
    state: str
    set_aside: str | None
    reservation: str | None
    value_min: float
    value_max: float
    posted_offset_days: int
    due_offset_days: int | None
    domain_key: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_profiles(*, profiles: int, tenants: int, seed: int) -> list[ProfileSpec]:
    """`profiles` profiles spread over `tenants` tenants, half us and half in."""
    import random

    rng = random.Random(seed * 31 + 7)
    tenant_regions = ["us" if index % 2 == 0 else "in" for index in range(tenants)]
    out: list[ProfileSpec] = []
    for index in range(profiles):
        tenant_index = index % tenants
        region = tenant_regions[tenant_index]
        pool = DOMAINS_BY_REGION[region]
        domain = pool[(index // tenants) % len(pool)]
        codes = (
            tuple(domain.naics) if region == "us" else (domain.gem_category, *domain.keywords[:2])
        )
        states = tuple(rng.sample(US_STATES if region == "us" else IN_STATES, 2))
        out.append(
            ProfileSpec(
                index=index,
                tenant_index=tenant_index,
                tenant_id=deterministic_id("tenant", seed, tenant_index),
                user_id=deterministic_id("user", seed, index),
                profile_id=deterministic_id("profile", seed, index),
                region=region,
                domain_key=domain.key,
                legal_name=f"{domain.name.split()[0]} Partners {index:04d} "
                f"{'LLC' if region == 'us' else 'Pvt Ltd'}",
                slug=f"load-{region}-{tenant_index:03d}",
                email=f"owner-{index:04d}@load.example",
                codes=codes,
                keywords=domain.keywords,
                exclude_keyword=domain.exclude,
                wanted_types=WANTED_TYPES[region][index % len(WANTED_TYPES[region])],
                states=states,
                buyer=BUYERS[region][index % len(BUYERS[region])],
            )
        )
    return out


def generate_opportunities(
    *, count: int, seed: int, noise_share: float = 0.30
) -> Iterator[OpportunitySpec]:
    """Notices with realistic distributions; yielded lazily so 50k never all sit in RAM."""
    import random

    rng = random.Random(seed)
    for index in range(count):
        region = "us" if index % 2 == 0 else "in"
        domain = rng.choice(DOMAINS_BY_REGION[region])
        notice_type = weighted_choice(rng, NOTICE_TYPES[region])
        status = weighted_choice(rng, STATUSES)
        buyer = rng.choice(BUYERS[region])
        state = rng.choice(US_STATES if region == "us" else IN_STATES)
        noun = rng.choice(domain.nouns)
        lot = rng.randrange(1, 40)
        title = f"{noun.title()} for {buyer} — Lot {lot} ({state})"
        description = (
            f"{domain.body} The period of performance is {rng.choice((12, 24, 36, 60))} months "
            f"at {state}. Offerors shall submit past performance for {rng.choice((2, 3, 5))} "
            f"similar efforts. Solicitation lot {lot}, sequence {index}."
        )
        if rng.random() < noise_share:
            naics: tuple[str, ...] = (rng.choice(NOISE_NAICS),)
        elif region == "us":
            naics = tuple(rng.sample(domain.naics, min(2, len(domain.naics))))
        else:
            naics = (rng.choice(domain.naics),)
        # 10% already past due, 8% with no published deadline, the rest over 90 days
        roll = rng.random()
        due: int | None
        if roll < 0.10:
            due = -rng.randrange(1, 45)
        elif roll < 0.18:
            due = None
        else:
            due = rng.randrange(1, 91)
        base = 50_000 if region == "us" else 2_000_000
        value_min = float(base * rng.randrange(1, 40))
        yield OpportunitySpec(
            index=index,
            opportunity_id=deterministic_id("opportunity", seed, index),
            source_id=rng.choice(SOURCES[region]),
            external_id=f"load-{seed}-{index:07d}",
            region=region,
            country="US" if region == "us" else "IN",
            currency="USD" if region == "us" else "INR",
            notice_type=notice_type,
            status=status,
            title=title,
            description=description,
            naics=naics,
            buyer=buyer,
            state=state,
            set_aside=rng.choice(SET_ASIDES) if region == "us" else None,
            reservation=rng.choice(RESERVATIONS) if region == "in" else None,
            value_min=value_min,
            value_max=value_min * rng.choice((2.0, 4.0, 8.0)),
            posted_offset_days=-rng.randrange(1, 120),
            due_offset_days=due,
            domain_key=domain.key,
        )


def search_terms() -> list[str]:
    """Query terms drawn from the same vocabulary the corpus is written from."""
    terms: list[str] = []
    for domain in DOMAINS:
        terms.extend(domain.keywords)
        terms.extend(domain.nouns)
    return terms


@dataclass(slots=True)
class Totals:
    """Accumulator shared by the score workers' JSON payloads."""

    profiles: int = 0
    pairs_scored: int = 0
    kept: int = 0
    filtered: int = 0
    created: int = 0
    updated: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    seconds: float = 0.0
    per_profile_seconds: list[float] = field(default_factory=list)

    def add(self, payload: dict[str, Any]) -> None:
        self.profiles += int(payload.get("profiles", 0))
        self.pairs_scored += int(payload.get("opportunities", 0))
        self.kept += int(payload.get("kept", 0))
        self.filtered += int(payload.get("filtered", 0))
        self.created += int(payload.get("created", 0))
        self.updated += int(payload.get("updated", 0))
        self.high += int(payload.get("high", 0))
        self.medium += int(payload.get("medium", 0))
        self.low += int(payload.get("low", 0))
        self.seconds += float(payload.get("seconds", 0.0))
        self.per_profile_seconds.extend(payload.get("per_profile_seconds", []))

    def as_dict(self) -> dict[str, Any]:
        per = self.per_profile_seconds
        return {
            "profiles": self.profiles,
            "pairs_scored": self.pairs_scored,
            "kept": self.kept,
            "filtered": self.filtered,
            "created": self.created,
            "updated": self.updated,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "worker_busy_seconds": round(self.seconds, 2),
            "per_profile_seconds_p50": round(percentile(per, 50), 3),
            "per_profile_seconds_p95": round(percentile(per, 95), 3),
        }


def score_shard(payload: dict[str, Any]) -> dict[str, Any]:
    """Score one shard of profiles. Module level so a spawned worker can import it.

    OQ-95: one process scores ~1,800 pairs/s, so 50k x 200 = 10 M pairs needs the batch
    fanned out. Profiles are embarrassingly parallel and `rescore_profile` is exactly the
    per-profile slice, so a shard is a list of (tenant_id, profile_id).
    """
    import asyncio

    return asyncio.run(_score_shard_async(payload))


async def _score_shard_async(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.embeddings import FakeEmbeddings
    from app.services.events import EventBus
    from app.services.matching.engine import MatchScorer

    quiet_logging()
    settings = build_settings(payload["app_url"], payload["owner_url"])
    database = build_database(settings)
    scorer = MatchScorer(
        settings=settings,
        database=database,
        embeddings=FakeEmbeddings(settings.embedding_dim),
        rationale=None,  # stage 3 is an LLM call; SPEC 12's load target is stages 1-2
        bus=EventBus(),  # no subscribers: notifications are M4-14's own budget
        page_size=int(payload["page_size"]),
    )
    totals: dict[str, Any] = {
        "worker": payload["worker"],
        "profiles": 0,
        "opportunities": 0,
        "kept": 0,
        "filtered": 0,
        "created": 0,
        "updated": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "per_profile_seconds": [],
        "errors": [],
    }
    started = time.perf_counter()
    try:
        for tenant_id, profile_id in payload["profiles"]:
            at = time.perf_counter()
            try:
                run = await scorer.rescore_profile(uuid.UUID(tenant_id), uuid.UUID(profile_id))
            except Exception as exc:  # a broken profile must not lose the whole shard
                totals["errors"].append(f"{profile_id}: {type(exc).__name__}: {exc}")
                continue
            data = run.as_dict()
            for key in (
                "profiles",
                "opportunities",
                "kept",
                "filtered",
                "created",
                "updated",
                "high",
                "medium",
                "low",
            ):
                totals[key] += int(data.get(key, 0))
            totals["per_profile_seconds"].append(round(time.perf_counter() - at, 3))
    finally:
        await database.dispose()
    totals["seconds"] = round(time.perf_counter() - started, 3)
    return totals


def utcnow() -> datetime:
    return datetime.now(UTC)


def offset_days(base: datetime, days: int | None) -> datetime | None:
    return None if days is None else base + timedelta(days=days)


def iter_specs(specs: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for spec in specs:
        batch.append(spec)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


__all__ = [
    "BACKEND_ROOT",
    "DEFAULT_SEED",
    "DOMAINS",
    "DOMAIN_BY_KEY",
    "FULL_OPPORTUNITIES",
    "FULL_PROFILES",
    "FULL_TENANTS",
    "MIN_SCALED_BUDGET_SECONDS",
    "REPO_ROOT",
    "SCORE_BUDGET_SECONDS",
    "SEARCH_P95_BUDGET_MS",
    "Domain",
    "OpportunitySpec",
    "ProfileSpec",
    "Stopwatch",
    "Totals",
    "build_database",
    "build_settings",
    "chunked",
    "database_arguments",
    "database_name",
    "deterministic_id",
    "emit",
    "generate_opportunities",
    "generate_profiles",
    "is_disposable",
    "iter_specs",
    "offset_days",
    "percentile",
    "progress",
    "quiet_logging",
    "scale_argument",
    "scaled",
    "score_shard",
    "search_terms",
    "shards",
    "utcnow",
    "weighted_choice",
    "write_report",
]
