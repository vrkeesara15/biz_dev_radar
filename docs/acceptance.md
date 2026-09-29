# MVP acceptance checklist (SPEC 12)

SPEC 12 ends with two lists that decide whether the MVP is done: **"Acceptance criteria
for MVP (all must pass)"** — seven boxes — and the **test plan** above it — eight
bullets. SPEC 13.6 then adds: *all tasks done or explicitly deferred by a human; CI green
on main; nightly smoke green 3 nights running; README with setup, env vars, adapter guide
and a runbook for a broken source.*

This page is one row per box and per bullet, with the test that proves it, the number
that test last measured, and what a person still has to do. It is deliberately
pessimistic: a row is only green where a test exercises the same code path the box
describes. **Nothing here has been observed against a live portal, a real mailbox, a real
payment page or a real Office install** — this repository has never been deployed.

Run it:

```bash
make acceptance                                # the automated subset, backend only
ACCEPTANCE_WITH_E2E=1 E2E_PORT=3111 make acceptance   # ... plus the Playwright + axe flows
python scripts/acceptance.py --list            # print the table, run nothing
```

`make acceptance` reads [`scripts/acceptance.json`](../scripts/acceptance.json) — the
row → pytest node id map — runs each row's node ids (each id at most once per run, so
`tests/isolation` is not paid for twice), prints the table with PASS / FAIL / MANUAL /
SKIP per row, and exits non-zero if any automated row is red. It is a gate, not a report.

It deliberately does **not** re-measure `core/` coverage (a figure taken over a subset of
the suite would be a lie — that gate is `make test`), run the load suite (`make
load-full` needs its own database and about half an hour), or claim any box that needs
the real world.

## Acceptance criteria for MVP

| Box (SPEC 12) | Proof | Automated status (last measured) | Manual steps remaining | Owner |
| --- | --- | --- | --- | --- |
| A new SAM.gov notice matching the internal profile appears in the app **and in Slack** within **60 minutes** | `tests/unit/test_acceptance_budgets.py::test_the_sam_poll_plus_the_notify_flush_fits_the_60_minute_box` (the schedule's ceiling) + `tests/integration/test_ingest_match_notify.py::test_ingest_to_match_to_notify_end_to_end` (the path: bell + rendered email + Slack Block Kit post) | **green** — 2 passed. `sam_opps` polls `*/30`, `notify:flush` runs `*/5`, so the worst case is **35 min of a 60 min budget**; the end-to-end test asserts all three channels | **Not observed live.** Watch one real notice: portal posting time → `opportunities.created_at` → the bell, the email and the Slack post. Needs `SAM_API_KEY` (absent locally) and a deployed environment | Backend / QA |
| A new CPPP or GeM tender matching an India test profile appears within **6 hours** | `tests/unit/test_acceptance_budgets.py::test_the_indian_portal_polls_fit_the_6_hour_box` + `tests/integration/test_india_checklist.py` | **green** — 22 passed. `cppp` and `gem` poll `0 */3`, so the worst case is **3 h 5 min of a 6 h budget** | **Not observed live.** [india-testing.md](runbooks/india-testing.md): three (portal time, ingest time, notify time) triples across CPPP, GeM and one GePNIC state on staging-in | Backend / QA |
| Clicking Pursue produces a compliance matrix + full draft + bid/no-bid scorecard **within 30 minutes**, with page citations | `tests/integration/test_matrix.py::test_matrix_maps_every_requirement_and_extracts_rules_and_checklist`, `test_drafters.py::test_one_drafter_per_volume_writes_grounded_versions`, `test_bid_no_bid.py::test_scorecard_is_stored_weighted_and_pauses_at_gate_one`, `test_pursuit_artifacts_api.py::test_list_returns_the_latest_version_of_every_kind` | **green** — 4 passed. Every requirement carries `document_id` + `page`; the drafters ground every company claim or emit `[NEEDS INPUT]`; the scorecard is stored as a versioned artifact and read back by the workspace | **The 30-minute wall clock is not measured.** Tests replay `FakeLLM`. Time one real pipeline run on a deployed environment and record the `agent_runs` row | Agents / QA |
| Deadline reminders fire on the ladder in the user's time zone; calendar events created | `tests/integration/test_reminders.py::test_the_ladder_is_generated_and_fires_rung_by_rung`, `::test_a_us_reader_and_an_ist_reader_are_reminded_at_the_same_instant`, `::test_the_ladder_survives_the_2026_11_01_fall_back`, `test_calendar_api.py::test_the_feed_is_issued_rotated_and_serves_the_users_dates`, `::test_google_push_creates_updates_and_deletes` | **green** — 5 passed. The 7d/3d/24h/4h/1h ladder, escalation, the DST fall-back, the signed per-user `.ics` feed and Google/Microsoft push | Subscribe a real Google/Outlook calendar to the feed and confirm the invite lands; part of the India pilot runs | QA |
| DOCX/PDF/XLSX/ZIP exports **open cleanly** in Word, Acrobat and Excel | `tests/integration/test_exports.py::test_the_docx_opens_in_word_and_carries_the_draft_footer`, `::test_the_pdf_opens_in_acrobat_and_carries_the_footer`, `::test_the_xlsx_opens_in_excel_with_matrix_checklist_and_pricing`, `::test_the_zip_uses_the_solicitations_file_naming_rule`, `::test_a_final_package_drops_the_draft_footer_but_keeps_the_disclaimer` | **green** — 5 passed. Each file is re-parsed with python-docx / pypdf / openpyxl / zipfile and the footer and disclaimer rules are checked | **"Opens cleanly" is an Office claim a parser cannot make.** Open all four in Microsoft Word, Adobe Acrobat and Microsoft Excel — not a viewer | QA |
| Cross-tenant test suite passes; audit log records every draft access | `tests/isolation` (the whole suite), `tests/unit/test_route_table.py`, `test_workspace_api.py::test_list_and_read_drafts_with_their_grounding_counts`, `test_pursuit_artifacts_api.py::test_scorecard_and_red_team_reads_are_audited_and_the_rest_are_not`, `tests/integration/test_audit.py` | **green** — **503 passed**. The harness enumerates `app.openapi()` and fails on any route without a factory, so a new route cannot skip the probe; `draft.read`, `draft.list`, `export.downloaded` and `pursuit_artifact.read` rows are asserted | None | Backend |
| India checklist above passes in **staging-in** | `tests/integration/test_india_checklist.py` + [india-testing.md](runbooks/india-testing.md) | **partial** — 22 passed: **14 of 30 rows are automated-green**, 16 are manual-pending | **staging-in has never been deployed.** WhatsApp template approval and delivery, SES Mumbai SPF/DKIM/DMARC, a Razorpay test payment with a GST invoice, live bucket/DB residency, Jio/Airtel latency, and the two pilot end-to-end bids | Ops / Pilot / Infra |

## Test plan

| Bullet (SPEC 12) | Proof | Automated status (last measured) | Manual steps remaining | Owner |
| --- | --- | --- | --- | --- |
| Unit: every normalizer, scorer, date/time-zone function, eligibility rule; **≥ 85% line coverage on `core/`** | `make test` (`--cov=app/core --cov-fail-under=85`) | **green** — **98.78%** over `app/core`, 3253 passed / 37 skipped | None. Not re-run by `make acceptance`: a coverage figure over a subset of the suite would be meaningless | Backend |
| Adapter contract tests: recorded fixtures per source, including a malformed page and a layout change | `tests/adapters` | **green** — 118 passed, 36 skipped (the skips are the documented stubs, `enabled = False`) | None | Backend |
| Live smoke (nightly): each adapter fetches ≥ 1 record from the real source; failure pages the admin channel | `tests/unit/test_smoke_job.py`, `tests/unit/test_nightly_smoke_config.py`, `make smoke` | **green for the code** — 6 passed. **The smoke itself has never run**: `make smoke` exits 0 without touching the network unless `BIDRADAR_LIVE=1`, and there is no `SAM_API_KEY` here | **SPEC 13.6 wants three green nights in a row; zero have run.** Set the secrets, enable `.github/workflows/nightly-smoke.yml`, and watch three nights | Ops |
| Integration: ingest → match → notify end-to-end on the local stack, **Mailpit asserting the email** | `tests/integration/test_ingest_match_notify.py` | **green** — 3 passed. The email is rendered and asserted in-process | The Mailpit leg (`tests/integration/test_notify_email.py`) **skips** when Mailpit is unreachable, and it skipped in the last full run. Bring `infra/docker-compose.yml` up to exercise it | Backend |
| Tenant isolation: two tenants, every endpoint cross-tenant; any 200 with foreign data fails the build | `tests/isolation` / `make isolation` | **green** — 503 passed | None | Backend |
| Agent evals: 10 US + 10 India golden notices; recall ≥ 90%, precision ≥ 85%, zero fabricated facts, every requirement cites a page, eligibility exact match ≥ 90% | `tests/evals` / `make eval` | **green** — 125 passed. Recall **93.8%**, precision **93.8%**, page citations **100%**, eligibility exact **100%** (56/56), fabricated facts **0** | **Measured against replayed answers, not a live model** ([evals.md](evals.md)). Run the set against the real model before launch | Agents |
| UI: Playwright flows for onboarding, search, pursue, review, export; **axe accessibility check** | `frontend/e2e/flows.spec.ts` + `frontend/e2e/axe.ts`; `pnpm e2e` | **green** — **56 passed** (the 5 flows + the settings/admin axe sweep + 50 existing specs), **zero serious/critical axe violations** over 24 analyses | Run with `ACCEPTANCE_WITH_E2E=1` (needs a Next dev server and Chromium). The flows run against the e2e mock API, not the backend | Frontend |
| Load: 50k opportunities, 200 profiles scored in **< 10 min**; search **p95 < 500 ms** | `tests/unit/test_load_scripts.py`, `make load-smoke` / `make load-full`, [scripts/load/README.md](../scripts/load/README.md) | **green** — 17 passed for the scripts. Measured at SPEC scale: scoring **461 s of a 600 s budget**; search **p95 156.8 ms** with `ix_matches_tenant_opportunity_score` (it was 538 ms before that index) | Not re-run by `make acceptance`: `make load-full` needs `bidradar_load` and about half an hour. Re-run after any matching or index change | Backend |

## SPEC 13.6 "Definition of done"

| Clause | State |
| --- | --- |
| All tasks done **or explicitly deferred by a human** | 113 of 113 tasks in [tasks.json](../tasks.json) are `done`. The work a task could not finish is recorded as an open question in [PROGRESS.md](../PROGRESS.md), not as a silent gap. The list below is what a human still owns |
| Section 12 acceptance boxes ticked | **6 of 7 automated-green; none observed live.** Box 7 (the India checklist in staging-in) is 14/30 rows green and the environment does not exist |
| CI green on main | The workflow runs lint, the test suite with the coverage gate, the isolation suite, the frontend lint/typecheck/unit/build and now the Playwright + axe flows, plus both Docker builds, Terraform validate and the security scan. **It has never been executed on a hosted runner** — there is no remote |
| Nightly smoke green 3 nights running | **Not started.** Zero nights |
| README with setup, env vars, adapter guide and a runbook for a broken source | Done: [README.md](../README.md), [docs/adapters.md](adapters.md), [docs/runbooks/broken-source.md](runbooks/broken-source.md) |

### Deferred to a human

Nothing below can be closed by the build loop. Each one needs a credential, a deployment,
an install, a person or a lawyer.

1. **Deploy anything at all.** No GCP project exists. Every box that says "not observed
   live" waits on this, and so do CI-on-a-runner and the nightly smoke.
2. **The SAM.gov API key** (OQ-3). Absent locally; the 60-minute box and the live smoke
   both need it.
3. **Three green nights of the live smoke** (SPEC 13.6). Needs 2 and 1.
4. **The India checklist in staging-in** — 16 manual rows in
   [india-testing.md](runbooks/india-testing.md): WhatsApp templates approved by Meta and
   delivered, SES Mumbai SPF/DKIM/DMARC and a mail-tester score, a Razorpay test payment
   with a GST invoice, live bucket and database locations from `gcloud`, Jio/Airtel p95
   page load, and two pilot users running a real GeM bid and a CPPP tender end to end.
5. **The restore drill** (OQ-83). `scripts/restore_drill.sh` has only been run with
   `--dry-run`; [runbooks/restore-drill.md](runbooks/restore-drill.md) has an empty drill
   log, so SPEC 11's "restore drill before launch" is **not** satisfied.
6. **Open the four exports in real Office applications.** The tests prove the files are
   well-formed, not that Word renders them.
7. **Run the eval set against the live model** ([evals.md](evals.md)); today's bars are
   measured over replayed answers.
8. **`NEXT_PUBLIC_API_BASE_URL` is baked in at build time** (OQ-77) — the one place the
   "same image everywhere" rule leaks. Decide: runtime variable, or build per
   environment.
9. **Legal and finance** (SPEC 14): counsel on commercial use of Indian portal data and
   DPDP, the terms of service and privacy policy ([legal.md](legal.md)); a CA on the GST
   treatment (OQ-59).
10. **Product decisions** (SPEC 14): the product name and domain (OQ-1), which company
    profile seeds the internal tenant (OQ-2), and confirming the three Indian state
    portals actually shipped — Tamil Nadu, Uttar Pradesh and central GePNIC rather than
    SPEC's proposed Telangana/Karnataka/Maharashtra (OQ-14).
11. **Attribution and source terms** (OQ-64 and [legal.md](legal.md)): the per-source
    attribution strings shown in the UI and exports are our reading of each portal's
    terms, not a reviewed one.

## The last run

`ACCEPTANCE_WITH_E2E=1 E2E_PORT=3111 make acceptance`, all rows:

```text
SPEC 12 — MVP acceptance checklist        (docs/acceptance.md)
====================================================================================================

Acceptance criteria for MVP
---------------------------
  PASS   A new SAM.gov notice matching the internal profile appears in the app and
         in Slack within 60 minutes
           -> 2 passed in 5.07s [6.0s]
           owner: Backend / QA
  PASS   A new CPPP or GeM tender matching an India test profile appears within 6
         hours
           -> 22 passed in 9.52s [10.2s]
           owner: Backend / QA
  PASS   Clicking Pursue produces a compliance matrix + full draft + bid/no-bid
         scorecard within 30 minutes, with page citations
           -> 4 passed in 5.62s [6.4s]
           owner: Agents / QA
  PASS   Deadline reminders fire on the ladder in the user's time zone; calendar
         events created
           -> 5 passed in 5.78s [6.7s]
           owner: QA
  PASS   DOCX/PDF/XLSX/ZIP exports open cleanly in Word, Acrobat and Excel
           -> 5 passed in 10.36s [11.0s]
           owner: QA
  PASS   Cross-tenant test suite passes; audit log records every draft access
           -> 503 passed in 145.99s (0:02:25) [147.6s]
           owner: Backend
  PASS   India checklist above passes in staging-in
           -> already run for an earlier row [0.0s]
           owner: Ops / Pilot / Infra

Test plan
---------
  MANUAL Unit: every normalizer, scorer, date/time-zone function, eligibility rule;
         >= 85% line coverage on core/
           -> make test
           .. Run `make test` -- the gate is `--cov=app/core --cov-fail-under=85`.
           .. Not re-run here because a coverage figure measured over a subset of the
           .. suite would be a lie.
           owner: Backend
  PASS   Adapter contract tests: recorded fixtures for each source (including a
         malformed page and a layout change) parse to the canonical schema
           -> 118 passed, 36 skipped in 3.03s [3.7s]
           owner: Backend
  PASS   Live smoke (nightly): each adapter fetches >= 1 record from the real
         source; failure pages the admin channel
           -> 6 passed in 0.20s [0.6s]
           owner: Ops
  PASS   Integration: ingest -> match -> notify end-to-end on the local stack with
         Mailpit asserting the email
           -> 3 passed in 5.56s [6.5s]
           owner: Backend
  PASS   Tenant isolation: two tenants, every endpoint probed cross-tenant; any 200
         with foreign data fails the build
           -> already run for an earlier row [0.0s]
           owner: Backend
  PASS   Agent evals: 10 US + 10 India golden notices; recall >= 90%, precision >=
         85%, zero fabricated facts, every requirement cites a page, eligibility
         exact match >= 90%
           -> 125 passed in 1.68s [2.4s]
           owner: Agents
  PASS   UI: Playwright flows for onboarding, search, pursue, review, export; axe
         accessibility check
           -> 56 passed (1.2m)
           owner: Frontend
  PASS   Load: 50k opportunities, 200 profiles scored in < 10 min; search p95 < 500
         ms
           -> 17 passed in 7.43s [8.4s]
           owner: Backend
====================================================================================================
14 automated row(s) green, 0 red, 0 skipped, 1 manual (of 15 rows).
The automated subset of the SPEC 12 acceptance checklist is green.
Manual rows are NOT ticked by this run: docs/acceptance.md has the steps.
```

"14 automated rows green" means the code paths behind fourteen rows do what the spec
says on this machine. It does **not** mean the MVP is accepted: six of the seven boxes
have a manual half that nobody has run, and the seventh needs an environment that does
not exist.
