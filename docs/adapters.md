# Source adapters: how to add one, and the rules every adapter follows

This is the working guide for `backend/app/adapters/`. The contract is SPEC section 5.1;
the compliance rules are SPEC sections 5.1 and 11. Read this before touching a portal.

When an adapter that used to work stops working, go to
[runbooks/broken-source.md](runbooks/broken-source.md) instead — that page is the triage
for a portal redesign, an IP block or a new CAPTCHA. This page is for building one.
The rest of the documentation is indexed in [index.md](index.md).

## 1. The contract

```python
class SourceAdapter(Protocol):
    source_id: str              # 'sam_opps', 'grants_gov', 'cppp', 'gepnic_tn', ...
    region: Literal['us', 'in']
    schedule: str               # 5-field cron; Celery beat builds its schedule from it

    def fetch(self, since: datetime, cursor: str | None) -> Iterator[RawRecord]: ...
    def fetch_detail(self, external_id: str) -> RawRecord: ...
    def fetch_documents(self, raw: RawRecord) -> list[DocumentRef]: ...
    def normalize(self, raw: RawRecord) -> OpportunityIn: ...
    def health(self) -> AdapterHealth: ...
```

* `app/adapters/base.py` holds the protocol, `RawRecord`, `AdapterHealth`,
  `DegradedNotes` and re-exports the canonical `OpportunityIn` (`app/core/opportunity.py`).
* `app/adapters/registry.py`: decorate the class with `@register`. Modules under
  `app/adapters/` are imported automatically (`load_builtin_adapters`), so a new file is
  enough. `enabled = False` keeps a source visible (health, `sources` row) but unscheduled.
* Adapters are synchronous and plain. All I/O goes through `PoliteClient`
  (`app/adapters/http.py`); the async pipeline (`services/source_runner.run_source`) owns
  the database, the watermark and the `source_runs` row.
* Constructor keyword arguments must all be optional (`client=None, settings=None,
  now=None, ...`): the scheduler, the CLI and the nightly smoke build adapters with no
  arguments.

### What `fetch()` must do

* Take `since` (watermark minus 2 days, `core/watermark.py`) and an opaque `cursor`
  string; yield `RawRecord(source_id, external_id, fetched_at, payload, raw_ref,
  meta={"cursor": ...})`. Put the next cursor in `meta["cursor"]` on every record so an
  aborted run resumes at the first unfinished page.
* Never yield a record without `external_id`.
* **A 200 page with a broken or changed layout is not an exception.** Skip the bad
  records, add a note (`self._notes.add(...)`) and let `health()` report `degraded`
  (see the four built-ins). Renamed result keys -> zero records + a `layout change?`
  note. The contract tests enforce this.
* HTTP errors, retry exhaustion and non-JSON bodies DO raise: the runner marks the run
  `failing`, keeps the watermark and cursor, and after more than two consecutive
  failing runs publishes `adapter.failing`.

### `normalize()`

Return `OpportunityIn`. Store everything time-related as tz-aware datetimes (the model
converts to UTC) and record the buyer's zone in `source_tz`. Put source-specific extras
in `extra` (JSON-safe). Documents go in `documents` as `DocumentRef(url, file_name,
kind, sha256, size, mime_type)`. Keep the mapping itself in `app/core/normalize/<source>.py`
(pure, unit-tested, counts toward the core coverage gate); the adapter class only wires
HTTP to it.

### `health()`

`ok` | `degraded` (page looked wrong, missing optional key, quota low) | `failing`
(last fetch raised) | `not_implemented` (stub) | `robots_disallowed`. Include a human
message; the admin console and the nightly smoke show it.

## 2. Politeness

`PoliteClient` gives you, per host: a token bucket (default 2 req/s, 1 req/s for
`*.gov.in`, overrides in `HTTP_RATE_LIMITS`), exponential backoff with full jitter on
429/5xx/transport errors honouring `Retry-After`, a daily quota for `api.sam.gov`
(`SAM_DAILY_QUOTA`), robots.txt checks and archiving of every response body under
`raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at}` (SPEC 5.1). The User-Agent is
`BidRadar/<version> (+mailto:<CONTACT_EMAIL>)`. Do not create your own httpx client.

Keep the window per request bounded (SAM: 365 days, awards: 1 year); paginate with the
source's own mechanism; stop when the source says so, never on a guessed count.

### The knobs, and what they raise

| Rule | Setting | Failure |
| --- | --- | --- |
| Token bucket per host | `HTTP_DEFAULT_RATE_PER_SEC` (2/s), `HTTP_GOV_IN_RATE_PER_SEC` (1/s for `*.gov.in`), `HTTP_RATE_LIMITS` (JSON map host → req/s) | none — the call simply waits |
| Retries with full jitter, honouring `Retry-After` | `HTTP_MAX_ATTEMPTS` (5), `HTTP_TIMEOUT_SECONDS` (30) | `RetryExhaustedError` after the last attempt |
| Daily quota (SAM.gov keys) | `SAM_DAILY_QUOTA` (10/day; `remaining_quota(host)` reads it back) | `QuotaExhaustedError` |
| robots.txt, fetched and cached per host, checked *before* the request | — | `RobotsDisallowedError` |
| Identifying User-Agent | `APP_VERSION`, `CONTACT_EMAIL` → `BidRadar/<version> (+mailto:<contact>)` | — |
| Raw payload archive | `STORAGE_BACKEND` and the region bucket; `MemoryArchiver` in tests, `NullArchiver` when archiving is off | — |

**Archive keys.** Every response body is stored verbatim, once, at
`raw/{source_id}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at}` in the bucket of the
deployment's residency region (SPEC 5.1). That path is the contract: it is what a replay
reads, what an audit cites, and what you pull when a portal changes shape and you need
the exact bytes the parser choked on. `RawRecord.raw_ref` carries the key, so a stored
opportunity can always be traced back to the page it came from.

All four errors above are `PoliteClientError` subclasses, and all of them escape
`fetch()` on purpose: the runner then marks the run `failing` and keeps the watermark,
so nothing is lost and the failure is visible. Do **not** catch them to return an empty
page.

## 3. Compliance rules (non-negotiable)

* Public pages and documented APIs only. Never log in to a portal on a user's behalf,
  never store portal passwords or DSC keys, never automate a submission.
* Never solve or bypass a CAPTCHA. If a detail page needs one, keep the tender id and
  the portal search URL and set `detail_status = "manual"`.
* Respect robots.txt (the client raises `RobotsDisallowedError` before the request).
  `mahatenders.gov.in` is `Disallow: /` and stays link-only (OQ-14).
* Follow each API's terms and quotas: one key per environment, no key sharing across
  tenants unless the terms allow a system account (SAM.gov: OQ-3).
* Paid aggregators only through licensed APIs (`app/adapters/paid_feeds.py`): the licence
  is a Secret Manager reference, never a value in code, config files or logs. A
  tenant-supplied licence makes the data tenant-scoped.
* Show source attribution and a link back to the official portal on every record
  (`core/attribution.py`), plus the disclaimer "Verify every detail on the official
  portal before submitting." (`core/disclaimers.py`).
* Get a written legal opinion before commercial resale of Indian portal data (SPEC 11).

## 4. Fixtures and tests

Record fixtures under `backend/tests/adapters/fixtures/<source_id>/`:

| file | purpose |
|---|---|
| `<first page>.json` (or `.html`) | a real capture (redact tokens, anonymise people); note synthesized fixtures in PROGRESS with an OQ |
| `malformed.json` | the same page with broken records: non-object entries, missing ids, wrong types, non-integer counts |
| `layout_change.json` | the page with the results key renamed |

Then:

1. Unit tests for `app/core/normalize/<source>.py` (every date format, money format,
   type code, edge case).
2. Adapter tests with `respx` (no network): pagination, cursor resume, 429 backoff,
   API error -> `failing`, quota, robots.
3. Add a `ContractSpec` in `backend/tests/adapters/contract.py`. The contract suite runs
   every registered adapter over the three fixture kinds and fails for a registered
   adapter without a spec. Disabled adapters are skipped explicitly.
4. `make smoke` (`BIDRADAR_LIVE=1`) must fetch at least one live record; the nightly
   workflow pages the ops channel otherwise. See "Live smoke" below for the India run.

## 5. Live smoke

`make smoke` runs `python -m app.jobs.smoke`, which walks the registry and asks every
**enabled** adapter for one record from the real source. It exits 0 without doing
anything unless `BIDRADAR_LIVE=1`, so it is safe on a laptop and in CI without keys.
The JSON report lists every adapter's outcome plus a `skipped` array naming each
registered-but-disabled source with its health status and reason (documented stubs, paid
feeds, and `gepnic_mh`, whose robots.txt is `Disallow: /`), so a source is never silently
absent.

The nightly GitHub workflow (`.github/workflows/nightly-smoke.yml`, 03:00 UTC) runs it
for the US sources and pages the ops channel on failure.

### The India run is manual

CPPP, GeM and several GePNIC state portals refuse connections from outside India
(PROGRESS OQ-14: `bidplus.gem.gov.in` refused the build host outright), so the India
smoke is **not** part of the nightly workflow. Run it by hand from an Indian IP or an
Indian cloud region (for example a `asia-south1` Cloud Run job or a VM in Mumbai),
before a release that touches an India adapter and after any portal redesign:

```bash
export BIDRADAR_LIVE=1
cd backend
uv run python -m app.jobs.smoke --days 7 \
  --only cppp --only gem \
  --only gepnic_tn --only gepnic_up --only gepnic_central
```

Expected: `"status": "ok"` and `records >= 1` for each of the five. What to do with the
output:

* an adapter that returns 0 records or `health: failing` means the portal changed -
  re-record its fixtures from the live pages (`tests/adapters/fixtures/<source_id>/`,
  all three kinds) and fix the parser; the contract suite is the regression test.
* `health: degraded` names the page that no longer parses; the run still passes because
  the primary page flowed.
* `gepnic_mh` and `gepnic_ts` must appear under `skipped`, never under `results`.
* the first successful India run also replaces the synthesized fixtures noted in
  PROGRESS OQ-60 / OQ-61 / OQ-62 / OQ-63 with real captures.

## 6. Wiring

* `sources` rows are synced from the registry on API start and before every job
  (`services/sources.sync_sources`); operators flip `enabled` per environment.
* Celery beat schedules `bidradar.run_source(<source_id>)` from `schedule`
  (`app/celery_app.py`); `python -m app.jobs.run_source <source_id>` is the Cloud Run
  job entrypoint; `POST /api/v1/admin/sources/{id}/run` runs one now.
* Special sinks: `usaspending` feeds `agency_spend_stats`, `sam_awards` feeds
  `awards_enrichment`; everything else goes through `services/ingest.ingest`
  (dedupe, versions, status, summary_ai).

## 7. Adding a GePNIC state portal (M3)

GePNIC portals all run the same NIC application, so one class
(`app/adapters/gepnic.py`, `GePNICAdapter`) serves every state; a portal is a row in
`backend/app/adapters/gepnic_configs.yaml` and a set of fixtures — no Python:

```yaml
portals:
  - source_id: gepnic_tn
    display_name: Tamil Nadu Tenders (tntenders.gov.in)
    state: Tamil Nadu
    portal_home: https://tntenders.gov.in/
    base_url: https://tntenders.gov.in/nicgep/app
    home_page: ""                                              # the front page marquee
    org_list_page: "?page=FrontEndTendersByOrganisation&service=page"
    latest_page: "?page=FrontEndLatestActiveTenders&service=page"   # CAPTCHA: never fetched
    search_page: "?page=FrontEndAdvancedSearch&service=page"        # human fallback
    date_formats: ["%d-%b-%Y %I:%M %p", "%d-%b-%Y"]
    tz: Asia/Kolkata
    schedule: "0 */3 * * *"
    enabled: true
    robots_checked: "2026-09-26"
```

Every entry is registered at import time (`PORTAL_ADAPTERS`), so the registry, the
`sources` rows, Celery beat, the admin health listing and the contract suite see it.
A portal that must not be crawled stays in the file with `enabled: false` plus
`health_status: robots_disallowed | not_implemented` and a `reason`; it keeps its source
id and attribution, is never requested, and reports that status from `health()`.

Per run an enabled portal reads two captcha-free pages through `PoliteClient`
(1 req/s for `*.gov.in`, robots.txt, raw archive):

1. the front page, whose "Latest Tenders" marquee gives title / reference / closing /
   opening (no tender id, no organisation);
2. "Tenders by Organisation": the organisation index, then the first
   `GEPNIC_MAX_ORGS_PER_RUN` organisation listings — the 6-column table with the GePNIC
   tender id (`2026_TNCMC_871234_1`) and the `||`-separated organisation chain.

Organisation listings are read first and the marquee is deduplicated against them on
reference + title, so the richer row (tender id, buyer hierarchy) wins. "Latest Active
Tenders" is CAPTCHA-gated on GePNIC and is never fetched (PROGRESS OQ-62). Per-tender
links are Tapestry `DirectLink`s bound to the visitor session and expire, so every record
is `detail_status = "manual"` with `extra.portal_search_url` pointing at the portal's own
search page; `fetch_detail` and `fetch_documents` make no request.

Checklist for a new portal: (1) robots.txt permits the listing pages (OQ-14 lists the
verified ones: tntenders.gov.in, etender.up.nic.in, etenders.gov.in; mahatenders.gov.in
is `Disallow: /`); (2) add the YAML row; (3) record `home.html`, `org_index.html`,
`org_listing.html` plus `malformed.html` and `layout_change.html` under
`backend/tests/adapters/fixtures/<source_id>/` (the contract suite builds a spec for every
enabled row automatically); (4) run the smoke from a cloud region (some portals refuse
non-Indian IPs).

## 8. Stubs and paid feeds

`app/adapters/stubs.py` (defense.gov awards, SLED generic, IREPS, defproc) and
`app/adapters/paid_feeds.py` (HigherGov, GovSpend, BidNet, TenderTiger, Tender247,
BidAssist) are registered with `enabled = False` and `health() == not_implemented`; each
class docstring records what is known about the source and what implementing it needs.
A stub is not a placeholder file: it is a registered source with an id, attribution and
a health entry, so the admin console and the smoke report can say "we know about this
source and it is off", which is very different from silence.

### How a paid feed plugs in

Paid aggregators are **licensed APIs, never scraped** (SPEC 1 "out of scope", SPEC 11).
`PaidFeedAdapter` in `app/adapters/paid_feeds.py` is the base, and its one structural
idea is that a licence key is a *reference*, never a value:

```python
@register
class HigherGovAdapter(PaidFeedAdapter):
    source_id = "highergov"
    region = "us"
    schedule = "0 */6 * * *"
    enabled = False
    vendor_url = "https://www.highergov.com/"
    licence_note = "tenant or platform API key; redistribution limited to the licensee's users"
    default_secret_ref = "HIGHERGOV_API_KEY"      # env var name locally
```

* `licence_secret_ref` is a Secret Manager resource name in the cloud
  (`projects/bidradar/secrets/highergov-key/versions/latest`, resolved by
  `GcpSecretResolver`) or an environment-variable name locally (`EnvSecretResolver`).
  `resolver_for(ref)` picks between them by prefix.
* `licence_key()` resolves it once and holds it on the instance. It is never logged,
  never put in a health message (`health()` says only "licence reference set" or "no
  licence reference") and never written to config or a manifest.
* A **tenant-supplied** licence means one adapter instance per tenant, constructed with
  that tenant's reference and `tenant_id`. The records it returns are then
  **tenant-scoped, not global** — so before any tenant-licensed feed is enabled, the
  ingest pipeline has to be given a tenant sink. Today it has a global one; this is the
  blocking piece, not the HTTP client.
* Respect the vendor's terms in `licence_note`: most forbid resale of raw records and
  limit redistribution to the licensee's own users. That constrains what may be shown
  to other tenants, exported, or cached — read it before you wire the sink.

To promote a stub or a paid feed: implement `fetch`/`normalize`, record the three
fixture kinds, add the `ContractSpec`, set `enabled = True`, and (for a paid feed) give
it a licence reference and confirm the sink is tenant-scoped.

## 9. Checklist for a new adapter PR

Work top to bottom; each line is something a reviewer will look for.

**Before any code**

- [ ] The source is a public page or a documented API, and the terms permit our use.
      Paid aggregator? Then it is a licensed API (section 8), not a crawl.
- [ ] `robots.txt` permits the exact paths you intend to fetch. Record the date checked
      (and for a GePNIC portal, put it in `robots_checked`).
- [ ] No page in the plan needs a CAPTCHA, a login or a session cookie we would have to
      obtain on a user's behalf. If the detail page does, the plan is listing-only plus
      `detail_status = "manual"`.
- [ ] The incremental strategy is written down: what the watermark is, what the cursor
      is, and how the 2-day overlap applies.

**The adapter**

- [ ] `app/adapters/<source_id>.py`, decorated with `@register`; `source_id`, `region`,
      `schedule` (5-field cron) and `enabled` set.
- [ ] Every constructor keyword argument is optional — the scheduler, the CLI and the
      smoke build it with no arguments.
- [ ] All I/O through `PoliteClient`; no `httpx.Client` of your own, no `requests`.
- [ ] `fetch()` yields `RawRecord`s with a non-empty `external_id` and the next cursor
      in `meta["cursor"]` on **every** record.
- [ ] A 200 page with a changed shape returns records-it-can-parse plus a note and
      `degraded` — it does not raise. Transport and API errors do raise.
- [ ] `health()` returns one of `ok` / `degraded` / `failing` / `not_implemented` /
      `robots_disallowed` with a human message.

**The parser**

- [ ] The mapping lives in `app/core/normalize/<source>.py` and is pure — no I/O, no
      clock, no settings lookup beyond what is passed in. It counts toward the ≥ 85%
      `app/core` coverage gate.
- [ ] Datetimes are tz-aware and `source_tz` records the buyer's zone. Indian date
      formats (`17-Jul-2026 08:23 PM`, `DD-MM-YYYY`) are covered by tests.
- [ ] Money carries its currency; INR values are not silently treated as USD.
- [ ] Source-specific extras go in `extra` (JSON-safe), not in new columns.

**Tests**

- [ ] Fixtures under `backend/tests/adapters/fixtures/<source_id>/`: the real capture,
      `malformed`, and `layout_change`. Tokens redacted, people anonymised, long arrays
      trimmed.
- [ ] A synthesized fixture (no live access) is declared in PROGRESS.md with an OQ
      number saying what it was derived from.
- [ ] Unit tests for the parser: every date format, money format, type code and edge
      case you saw in the capture.
- [ ] Adapter tests with `respx`: pagination, cursor resume, 429 backoff, API error →
      `failing`, quota, robots.
- [ ] A `ContractSpec` in `backend/tests/adapters/contract.py` — a registered adapter
      without one fails the suite on purpose.
- [ ] `make lint && make test` green.

**Wiring and operations**

- [ ] `.env.example` and `app/core/config.py` carry any new setting (URLs, per-run
      caps); nothing is hard-coded.
- [ ] The Cloud Run manifests are regenerated
      (`cd backend && uv run python -m app.jobs.generate_cloudrun`) and committed —
      `tests/unit/test_cloudrun_manifests.py` fails otherwise.
- [ ] Attribution and the verify-on-portal disclaimer resolve for the new `source_id`.
- [ ] The smoke covers it: it appears under `results` when enabled, or under `skipped`
      with a reason when not. For an India source, note in the PR that the live smoke
      is the manual `asia-south1` run.
- [ ] This guide is updated if the source introduces a new pattern, and
      [runbooks/broken-source.md](runbooks/broken-source.md) still describes how to
      triage it when it breaks.
