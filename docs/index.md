# BidRadar documentation

Every document in this repository, and when you would reach for it. The spec itself
([../SPEC.md](../SPEC.md)) is authoritative; these pages describe what was actually
built, including where it falls short of the spec.

`backend/tests/unit/test_docs_links.py` fails the build when a link here breaks, when a
runbook is not listed on this page, or when the README's environment table stops
matching `app/core/config.py`.

## Start here

| Page | What it covers |
| --- | --- |
| [../README.md](../README.md) | What BidRadar is, the six layers, repo layout, local setup, every environment variable, how to run the tests, what is and is not built yet |
| [../SPEC.md](../SPEC.md) | The requirements spec: sources, schema, matching, agents, notifications, security, deploy and the build plan |
| [../CLAUDE.md](../CLAUDE.md) | The rules the build loop is bound by (tests first, never weaken a test, no CAPTCHA/login/auto-submit) |
| [../PROGRESS.md](../PROGRESS.md) | The append-only build log and the **Open questions** — the honest state of every ambiguous decision |

## Building on the system

| Page | What it covers |
| --- | --- |
| [adapters.md](adapters.md) | **The adapter guide.** The `SourceAdapter` protocol, `PoliteClient` rules, fixtures and contract tests, the nightly smoke, adding a GePNIC state through YAML, plugging in a paid feed, the compliance rules, and the checklist for a new adapter PR |
| [../frontend/README.md](../frontend/README.md) | Frontend environment, Auth.js wiring, the generated API client, the screens built so far |
| [../infra/terraform/README.md](../infra/terraform/README.md) | The four environments as Terraform, what each module creates, residency enforcement |
| [../infra/cloudrun/README.md](../infra/cloudrun/README.md) | The generated Cloud Run services and per-adapter jobs, and the one-image/six-modes entrypoint |
| [../scripts/load/README.md](../scripts/load/README.md) | **The load tests.** The SPEC 12 targets (50k x 200 in under 10 minutes, search p95 under 500 ms), how to run `make load-smoke` / `make load-full`, the measured numbers and the extrapolation |
| [acceptance.md](acceptance.md) | **The MVP acceptance checklist.** Every SPEC 12 acceptance box and test-plan bullet with the test that proves it, the number that test last measured, what a person still has to do, and the SPEC 13.6 definition of done with the list deferred to a human |
| [evals.md](evals.md) | **The eval set.** The SPEC 12 extraction and grounding bars, the 20 golden notices (10 US, 10 India), how `make eval` scores them, how to regenerate a notice and how to run the set against the live model |

## Runbooks

| Runbook | When you need it |
| --- | --- |
| [runbooks/broken-source.md](runbooks/broken-source.md) | A portal changed its layout, an API started erroring, the nightly smoke went red, or `adapter.failing` fired |
| [runbooks/deploy.md](runbooks/deploy.md) | Shipping to dev, promoting a tag to staging and production, deploying by hand, rolling back |
| [runbooks/india-testing.md](runbooks/india-testing.md) | **The SPEC 12 India checklist.** Every item with its owner, the automated test id or the step-by-step manual procedure (WhatsApp templates, SES Mumbai, Razorpay GST, residency, ISP latency, the two pilot bids), and the evidence to capture |
| [runbooks/observability.md](runbooks/observability.md) | Turning traces, errors and LLM traces on, and answering "what happened to this request?" |
| [runbooks/restore-drill.md](runbooks/restore-drill.md) | Backups, PITR, the restore drill, and the real restore when the day comes |

## Security, privacy and legal

| Page | What it covers |
| --- | --- |
| [../SECURITY.md](../SECURITY.md) | How to report a vulnerability, and the security posture in brief |
| [security/asvs-l2.md](security/asvs-l2.md) | The OWASP ASVS 4.0.3 Level 2 checklist, one section per chapter, with the gaps named rather than hidden |
| [privacy/dpdp-notice.md](privacy/dpdp-notice.md) | The DPDP consent notice shown to Indian tenants at signup |
| [privacy/sub-processors.md](privacy/sub-processors.md) | The sub-processor list, also served at `GET /api/v1/privacy` so the two cannot drift |
| [legal.md](legal.md) | What we crawl and on what basis, portal terms, attribution, and every item that needs written counsel |
