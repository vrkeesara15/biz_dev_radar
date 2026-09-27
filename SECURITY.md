# Security policy

BidRadar handles unreleased bid strategy and company financials. SPEC §11 treats tenant
isolation, encryption and the audit trail as launch blockers rather than later work, and
we would rather hear about a problem early and awkwardly than late and politely.

## Reporting a vulnerability

Email **security@bidradar.example** with:

- what you found and where (URL, endpoint, or file and line),
- how to reproduce it, ideally the smallest case that shows the problem,
- what you think an attacker could do with it.

If you need to send something sensitive, say so in a first message and we will arrange an
encrypted channel.

**Please do not** open a public GitHub issue for a vulnerability, test against another
tenant's data, or run automated scanners against the production environments
(`app.bidradar.example`, `in.bidradar.example`). If you need a live target to
demonstrate something, ask and we will give you a scratch tenant in `dev`.

### What to expect

| | |
| --- | --- |
| Acknowledgement | within 2 working days |
| Initial assessment | within 5 working days |
| Fix or mitigation for a critical issue | within 7 days of confirmation |
| Fix for everything else | with the next release, and we will tell you which |

We will credit you in the release notes unless you would rather we did not. We do not
currently run a paid bounty.

## Scope

**In scope**

- The API (`/api/v1/*`) and the web application.
- The container images in `backend/` and `frontend/`.
- The Terraform in `infra/terraform/` — an IAM binding that grants more than it should is
  a security issue, not a style issue.
- The CI and deploy workflows in `.github/`, including anything that could let a pull
  request obtain deploy credentials.

**Out of scope**

- Findings against third-party services we depend on (Google Cloud, Anthropic, Stripe,
  Razorpay, Langfuse, Sentry) — report those to them; our sub-processor list is in
  `docs/privacy/sub-processors.md`.
- The public procurement portals we read. We crawl them as anonymous public pages only:
  no login automation, no CAPTCHA solving, robots.txt respected (SPEC §11).
- Missing security headers on the API's JSON responses, denial of service by volume, and
  social engineering. The first is a known gap, tracked below.
- Reports that consist only of a scanner's output with no demonstrated impact.

## Known gaps

We would rather publish these than have you find them and wonder whether we knew.
The full checklist, with evidence for every control, is
[`docs/security/asvs-l2.md`](docs/security/asvs-l2.md); the open items today are:

1. No security headers or Content-Security-Policy are set (ASVS V14.4).
2. No address filtering on tenant-supplied URLs, so SSRF at a private address is not
   ruled out by the application layer (V12.6).
3. No written threat model (V1.14).
4. JWTs cannot be revoked before they expire (V3.8).
5. `FIELD_ENCRYPTION_KEY` rotation would need a re-encryption pass (V6.4.2).
6. `/docs` and `/openapi.json` are served in every environment (V14.3).

## What we do

Briefly, so you know what is already there and can aim past it:

- **Tenant isolation** is Postgres row-level security keyed on
  `current_setting('app.tenant_id')`, plus application-layer checks. A test suite
  enumerates `information_schema` and fails the build if any tenant table lacks a policy,
  and another calls every endpoint cross-tenant and fails on any 200 carrying foreign
  data.
- **Encryption**: TLS 1.2+ everywhere, CMEK on every bucket with the key in the bucket's
  own region, and AES-256-GCM field encryption for EIN, PAN, GSTIN, TAN and bank details.
- **Secrets** live in Secret Manager, one per setting; Terraform holds no value it did not
  generate, and GitHub Actions authenticates by Workload Identity Federation, so no
  service-account JSON key exists.
- **Uploads** are size- and content-type-checked and scanned by ClamAV before parsing; an
  unreachable scanner fails closed.
- **Rate limiting** is a Redis token bucket per tenant and per IP, plus a separate limiter
  on failed authentication.
- **Audit**: every mutating request and every admin support access writes an append-only
  `audit_log` row (who, what, when, IP, request id) that survives a tenant erasure.
- **Supply chain**: Dependabot weekly, `pip-audit --strict` and
  `pnpm audit --audit-level=high` in CI, `gitleaks` over full history, immutable image
  tags, and promotion by digest so production runs exactly the bytes staging ran.
- **Data residency**: Indian tenants' data stays in `asia-south1` — bucket, CMEK key,
  database, backups and Secret Manager replicas — and `terraform output residency_ok`
  is the standing check.

## Supported versions

BidRadar is a hosted service. We support the version currently deployed; there are no
long-term-support branches, and a fix ships to `prod-us` and `prod-in` from the same
image digest.
