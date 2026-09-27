# Source adapters: how to add one, and the rules every adapter follows

This is the working guide for `backend/app/adapters/`. The contract is SPEC section 5.1;
the compliance rules are SPEC sections 5.1 and 11. Read this before touching a portal.

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
   workflow pages the ops channel otherwise.

## 5. Wiring

* `sources` rows are synced from the registry on API start and before every job
  (`services/sources.sync_sources`); operators flip `enabled` per environment.
* Celery beat schedules `bidradar.run_source(<source_id>)` from `schedule`
  (`app/celery_app.py`); `python -m app.jobs.run_source <source_id>` is the Cloud Run
  job entrypoint; `POST /api/v1/admin/sources/{id}/run` runs one now.
* Special sinks: `usaspending` feeds `agency_spend_stats`, `sam_awards` feeds
  `awards_enrichment`; everything else goes through `services/ingest.ingest`
  (dedupe, versions, status, summary_ai).

## 6. Adding a GePNIC state portal (M3)

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

## 7. Stubs and paid feeds

`app/adapters/stubs.py` (defense.gov awards, SLED generic, IREPS, defproc) and
`app/adapters/paid_feeds.py` (HigherGov, GovSpend, BidNet, TenderTiger, Tender247,
BidAssist) are registered with `enabled = False` and `health() == not_implemented`; each
class docstring records what is known about the source and what implementing it needs.
To promote one: implement `fetch`/`normalize`, record fixtures, add the ContractSpec,
set `enabled = True` (and for a paid feed give the tenant a licence reference).
