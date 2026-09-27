# Runbook — a broken source

A procurement portal is not an API with a contract. It gets redesigned on a Tuesday
without notice, an endpoint starts returning 403 from outside India, a column moves, a
CAPTCHA appears where there was none. This page is what to do about it.

The guiding rule: **a broken source must never look like an empty world.** An adapter
that stops parsing has to be visible as `degraded` or `failing`, keep its watermark, and
leave the last good data in place. If you find a source that failed silently, that is a
bug in the adapter, not just in the portal — fix it here too.

Background reading: [../adapters.md](../adapters.md) (the contract, the politeness rules
and the fixture layout) and SPEC sections 5.1 and 12.

## 1. Signals

| Signal | Where it shows up | What it means |
| --- | --- | --- |
| **Nightly smoke failed** | `nightly-smoke` workflow red; a Slack message to the ops channel quoting the failing source ids | An enabled adapter returned zero records or raised against the live source. This is the usual first warning. |
| **`adapter.failing` event** | `app/services/source_runner.py` publishes it after **more than two** consecutive failing runs; the structured log line is `adapter.failing` with `source` and `consecutive_failures` | `fetch()` itself raised — transport error, HTTP error, retries exhausted, or a non-JSON body. The watermark and cursor were kept. |
| **`source_runs` rows** | `GET /api/v1/admin/sources/{source_id}/runs`, or the table directly | `status` of `degraded` (parsed, but something was wrong) or `failing`, plus the `errors` JSON array with the last messages. |
| **Admin console health** | `GET /api/v1/admin/health` → the `adapters` array; `GET /api/v1/admin/sources` for the full list | Per source: `health_status` (`ok` / `degraded` / `failing` / `not_implemented` / `robots_disallowed`), `health_message`, `last_run_at`, `last_status`, `consecutive_failures`. |
| **Silence** | An enabled source whose `last_run_at` is old, or whose ingest counts went to zero without a status change | The schedule stopped (Cloud Scheduler, beat, or the job image) — a deploy problem, not a portal problem. Check the Cloud Run job executions before touching the parser. |

Two failure classes are deliberately kept apart (PROGRESS OQ-48), and they need
different responses:

* **A 200 page that no longer parses** — renamed keys, a changed column order, a new
  wrapper element. `fetch()` does **not** raise: it skips the bad records, adds a note
  and reports `degraded`. Zero records plus a `layout change?` note is the signature of
  a redesign. *This is the case this runbook is mostly about.*
* **A transport or API error** — 403, 429 past the retries, 5xx, a non-JSON body, DNS.
  These raise, the run is `failing`, and the watermark is preserved so nothing is lost
  when the portal comes back.

```sql
-- the last ten runs of one source, newest first
SELECT started_at, status, fetched, upserted, errors
FROM source_runs WHERE source_id = 'gepnic_tn'
ORDER BY started_at DESC LIMIT 10;

-- every source that is not ok
SELECT source_id, enabled, health_status, health_message, last_status, consecutive_failures
FROM sources WHERE health_status <> 'ok' ORDER BY source_id;
```

## 2. Triage

### Step 1 — reproduce it locally

```bash
cd backend
uv run python -m app.jobs.run_source gepnic_tn      # prints the source_runs summary as JSON
```

The CLI runs the real adapter against the real portal through `PoliteClient`, writes a
`source_runs` row and prints `{source_id, run_id, status, fetched, upserted, errors,
last_error, watermark, mode}`. Exit status 1 means the run was `failing`.

The same job entrypoint is what the Cloud Run job and Celery run, so reproducing here
reproduces production. To run it against a live portal from the right country, run the
job in the region instead:

```bash
gcloud run jobs execute bidradar-staging-in-gepnic-tn --region asia-south1 --wait
```

If the source is Indian and you are not in India, expect connection refusals from the
build host — that is PROGRESS OQ-14, not a layout change. Check from a `asia-south1` job
before concluding anything.

### Step 2 — decide which failure you have

| What you see | Likely cause | Go to |
| --- | --- | --- |
| `status: degraded`, `fetched: 0`, note mentions a missing key or column | Layout change | step 3 |
| `status: degraded`, some records parsed | Partial layout change or a new optional field | step 3 |
| `status: failing`, `403` / `503` / connection refused | IP block, WAF, or the portal is down | step 6 |
| `status: failing`, `429` after retries | We are being rate limited | step 6 |
| `RobotsDisallowedError` | The portal's robots.txt changed | step 6 |
| An HTML CAPTCHA page where data used to be | CAPTCHA gating | step 6 |

### Step 3 — capture a fresh fixture

Fetch the page exactly as the adapter does — same URL, same headers, one request — and
save it next to the existing fixtures. Use the polite client so you do not hammer a
portal that is already unhappy:

```bash
cd backend
uv run python - <<'PY'
from app.adapters.http import MemoryArchiver, PoliteClient
from app.core.config import get_settings
from pathlib import Path

client = PoliteClient(get_settings(), archiver=MemoryArchiver())
resp = client.get("https://tntenders.gov.in/nicgep/app?page=FrontEndTendersByOrganisation&service=page")
Path("tests/adapters/fixtures/gepnic_tn/org_index.new.html").write_text(resp.text)
print(resp.status_code, len(resp.text))
PY
```

If the run already happened in a deployed environment, you do not need to fetch at all:
every response body is archived verbatim under
`raw/{source}/{yyyy}/{mm}/{dd}/{external_id}/{fetched_at}` in the region's bucket
(SPEC 5.1). Pull the newest object and use that — it is the exact bytes the parser
choked on.

**Redact before committing.** Strip API keys and session tokens from URLs and bodies,
anonymise names and contact details of real people, and trim very long arrays. If a
fixture had to be synthesized rather than captured, say so in PROGRESS.md with an OQ
number, the way OQ-35, OQ-40, OQ-61 and OQ-63 do.

### Step 4 — diff against the `layout_change` fixture

Every source carries three fixture kinds (see [../adapters.md](../adapters.md#4-fixtures-and-tests)):

```
backend/tests/adapters/fixtures/<source_id>/
  <first page>.json|.html     the known-good capture
  malformed.json|.html        a 200 page with broken records: non-objects, missing ids, wrong types
  layout_change.json|.html    the same page with the result key / column headers renamed
```

The `layout_change` fixture is what tells you whether the parser is *supposed* to cope:

```bash
cd backend/tests/adapters/fixtures/gepnic_tn
diff <(python3 -m json.tool org_index.html 2>/dev/null || cat org_index.html) org_index.new.html | head -50
# and, to see what the adapter treats as a redesign:
diff org_index.html layout_change.html | head -30
```

Read the diff with three questions:

1. **Is the change cosmetic or structural?** A renamed CSS class that the parser does
   not use is nothing. A renamed result key, a new wrapper object, a reordered or
   inserted table column is a structural change and needs a parser fix.
2. **Is the old shape still served anywhere?** Portals often ship the redesign on one
   page and not another. If so, keep both shapes supported — the parser should accept
   old and new for at least one release, so a rollback does not break ingestion.
3. **Did the meaning change?** A column that used to be "closing date" and is now
   "opening date" is far worse than a missing column, because it parses cleanly and is
   wrong. Check a handful of records against the portal by eye.

### Step 5 — fix, test, re-enable

The parser lives in `backend/app/core/normalize/<source>.py` (pure, unit-tested, counted
in the ≥ 85% core coverage gate); the adapter class only wires HTTP to it. Fix the
parser, not the adapter, unless the request shape itself changed.

```bash
cd backend
# 1. replace the good fixture with the new capture, and re-derive the other two
mv tests/adapters/fixtures/gepnic_tn/org_index.new.html tests/adapters/fixtures/gepnic_tn/org_index.html
#    then update malformed.html and layout_change.html from the NEW shape

# 2. unit tests for the parser first (a regression test for exactly what broke)
uv run pytest tests/unit/test_normalize_gepnic.py -q

# 3. the adapter tests (respx, no network) and the contract suite
uv run pytest tests/adapters -q

# 4. the whole gate
cd .. && make lint && make test
```

The contract suite (`backend/tests/adapters/contract.py`) is the regression test that
matters: it runs every registered adapter over all three fixture kinds and asserts that
the normal page yields records that normalize to `OpportunityIn`, that the malformed
page yields `degraded` without raising, and that the layout-change page yields zero
records, no exception, and a health message naming the missing key. A registered
adapter without a `ContractSpec` fails on purpose.

Then confirm against the live portal and put the source back:

```bash
cd backend
BIDRADAR_LIVE=1 uv run python -m app.jobs.smoke --days 7 --only gepnic_tn
uv run python -m app.jobs.run_source gepnic_tn          # a real run; watch fetched/upserted
```

If you disabled the source in step 6, undo that now — resume the Cloud Scheduler trigger
or flip `enabled` back and redeploy — and confirm `GET /api/v1/admin/health` shows `ok`
and `consecutive_failures: 0`. The counter resets on the first successful run.

Finally, ship the fix through the normal pipeline: a merge to `main` deploys dev, and a
tag promotes the same digest onwards ([deploy.md](deploy.md)). Do not hand-patch a
production revision.

### Step 6 — when you cannot fix it now

Stop the bleeding first. Disabling a source is cheap and reversible; leaving a failing
job to retry every three hours against an unhappy portal is neither polite nor useful.

**Disable at the right level:**

| Level | How | When |
| --- | --- | --- |
| **Stop one environment's schedule, no code change** | Pause the per-adapter Cloud Scheduler trigger: `gcloud scheduler jobs pause <prefix>-<source_id> --location <region>` (one trigger per adapter, created by `infra/terraform/modules/scheduler`). Resume with `gcloud scheduler jobs resume`. | You want production quiet for a few hours or days while dev keeps trying, and you do not want to ship a release to get there. |
| **Whole adapter, everywhere** | `enabled = False` on the class in `backend/app/adapters/<source>.py`, then merge and deploy. | The source is withdrawn or broken for longer than a few days. It stays registered and visible in `GET /api/v1/admin/sources`, with `health()` explaining why. This is the switch Celery beat, the CLI, the smoke and the manifest generator all honour. |
| **GePNIC state portal** | `enabled: false` in `backend/app/adapters/gepnic_configs.yaml`, plus `health_status: robots_disallowed \| not_implemented` and a `reason`. | A state portal must not be crawled at all. The row stays, so the source keeps its id, its attribution and its health entry. |
| **Config only** | Blank or repoint the relevant URL setting (`CPPP_BY_ORG_URL` disables that secondary page when empty), or lower `HTTP_RATE_LIMITS` for the host. | One page of a multi-page adapter broke, or we are being throttled. |

A registry-level disable removes the source from Celery beat and from the smoke's
`results`, drops its Cloud Run job from the generated manifests, and makes it appear in
the smoke report's `skipped` array with its reason — so it is visibly off, never
silently absent.

> **Known gap.** The `sources.enabled` column is *reported* by
> `GET /api/v1/admin/sources` but is **not** read by the scheduler, by
> `app/jobs/run_source.py` or by the smoke — all three ask the registry. Setting it to
> `false` therefore documents an intent without stopping anything. Until that is wired
> (PROGRESS OQ-85), use the Cloud Scheduler pause or the registry flag above, not the
> column.

**Never** delete the `sources` row: the watermark and the run history are how the next
person understands what happened.

A note on rollback: rolling the *application* back does not fix a portal redesign — the
old code parses the old page, which no longer exists. Roll back only when a BidRadar
deploy is what broke the adapter (check whether the first failing run lines up with a
deploy). Otherwise the fix is forward, with the source disabled in the meantime.

## 3. Communication

1. **Acknowledge in the ops Slack channel.** The nightly smoke posts there through
   `OPS_SLACK_WEBHOOK_URL` (`.github/workflows/nightly-smoke.yml`); reply in the thread
   with the source id, the class of failure, and whether the source is now disabled.
   Say when data last flowed — for most sources the user-visible symptom is "no new
   tenders from X since Tuesday", and that is the sentence people need.
2. **Say what users lose.** Existing opportunities from that source stay searchable;
   what stops is *new* notices and *amendments*. For a portal that is `detail_status =
   "manual"` anyway (CPPP detail pages, GePNIC expired links), the loss is smaller than
   it looks.
3. **Open an issue with the diff** — the fixture before and after, the failing
   `source_runs.errors` entry, and the decision taken. A portal that redesigns once
   redesigns again; the diff is the most useful thing to have next time.
4. **Record a decision in PROGRESS.md** if it changes what we support (a portal that
   must be dropped, a robots.txt that now disallows us, a page that became CAPTCHA
   gated). Add it as a new `OQ-<n>` so the README's status section can point at it.
5. **Escalate to the owner** when the cause is legal or contractual rather than
   technical: robots.txt now disallowing us, a terms change, or a portal asking us to
   stop. That is a step-6 disable *plus* a conversation, not an engineering fix.

> `adapter.failing` is published on the event bus and has a notification template
> (`app/notify/render.py`, category "source health alerts"), but **nothing subscribes it
> to a channel yet** — the alert delivery is part of the unfinished M4 notification work.
> Today the paging path is the nightly smoke's Slack webhook plus the `adapter.failing`
> log line. Until that gap closes, a source that breaks between two nightly runs is
> found by the next nightly run, not sooner.

## 4. India-specific notes

Indian portals fail in ways the US APIs do not. Check these before assuming a layout
change.

**CAPTCHA gating.** GePNIC's "Latest Active Tenders" page is CAPTCHA-gated and is never
fetched (PROGRESS OQ-62); CPPP detail pages are too. If a page that used to be
captcha-free starts returning a CAPTCHA form, the response is **not** to work around it:
we never solve or bypass a CAPTCHA, ever (SPEC 5.1, CLAUDE.md). Instead:

* stop fetching that page (config or adapter change);
* keep the tender id and the portal's own search URL and set
  `detail_status = "manual"` so a human can open it;
* if the *listing* page is gated and there is no captcha-free alternative, the source
  becomes link-only: disable it, keep the row, set `health_status` with a reason.

**robots.txt changes.** `PoliteClient` re-checks robots.txt and raises
`RobotsDisallowedError` before the request, so a portal that starts disallowing us fails
loudly rather than being crawled anyway. That is correct behaviour, not a bug. Record
the date you re-checked (`robots_checked` in `gepnic_configs.yaml`), disable the portal,
and tell the owner. `mahatenders.gov.in` is the standing example: `Disallow: /`, so
`gepnic_mh` is registered, never crawled, and reports `robots_disallowed` (OQ-14).

**IP blocks and geo-fencing.** Several Indian portals refuse connections from outside
India — `bidplus.gem.gov.in` refused the build host outright (OQ-14). A 403 or a
connection reset from a laptop or a GitHub runner therefore proves nothing. Confirm from
an Indian IP before you touch the parser:

```bash
# run the smoke as an asia-south1 Cloud Run job
gcloud run jobs execute bidradar-staging-in-smoke --region asia-south1 --wait
gcloud run jobs executions logs read <execution> --region asia-south1
```

This is also why the India half of the nightly smoke is a documented **manual** run
rather than part of the workflow ([../adapters.md](../adapters.md#the-india-run-is-manual)).
If a portal becomes reachable only from India, the fix is to move that source's smoke
(and, if needed, its ingestion job) to `asia-south1` — the Cloud Run jobs are generated
per adapter and per region already, so this is a registry and Terraform change, not new
code.

**Rate limits.** `*.gov.in` hosts are fetched at 1 req/s by default
(`HTTP_GOV_IN_RATE_PER_SEC`); if a portal starts 429-ing, lower it further for that host
through `HTTP_RATE_LIMITS` (`{"tntenders.gov.in": 0.5}`) rather than adding retries.
Also lower `GEPNIC_MAX_ORGS_PER_RUN` / `CPPP_MAX_ORGS_PER_RUN`: fewer organisation
listings per run is a smaller footprint and a shorter job.

**Bilingual content.** A page that suddenly parses to empty titles may have switched to
Devanagari or to a bilingual layout rather than changed structure. Dates are the other
recurring trap: GePNIC serves `17-Jul-2026 08:23 PM`, CPPP serves `DD-MM-YYYY`, and a
portal that changes format silently will parse to the wrong year. The date formats are
per-portal configuration (`date_formats` in `gepnic_configs.yaml`), so this is usually a
YAML change plus a unit test, not a parser rewrite.

## 5. After the incident

- The fixtures are updated and committed, so the contract suite would catch this exact
  regression again.
- A unit test exists for the specific thing that broke.
- The source is enabled, `health_status` is `ok` and `consecutive_failures` is 0.
- The nightly smoke has been green once for that source (for an India source, the manual
  run from `asia-south1`).
- PROGRESS.md records anything that changed what we support.
