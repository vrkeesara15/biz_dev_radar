# BidRadar frontend

Next.js 15 (App Router) · TypeScript · Tailwind v4 · shadcn/ui · Auth.js v5 ·
openapi-fetch client generated from the backend OpenAPI spec.

## Run

```sh
pnpm install
cp .env.example .env.local        # then fill in AUTH_SECRET at minimum
pnpm dev                          # http://localhost:3000
```

`/` redirects to `/app` when signed in, otherwise to `/signin`. `/app/*` is
protected by `src/middleware.ts`.

## Environment variables

All are listed with placeholders in `.env.example`; none are committed.

| Variable | Purpose |
| --- | --- |
| `AUTH_SECRET` | Auth.js session secret. **Shared with the backend**, which verifies API bearer tokens (HS256) with it. `openssl rand -base64 32` |
| `AUTH_URL`, `AUTH_TRUST_HOST` | Public URL / trust `Host` header when not on Vercel |
| `EMAIL_SERVER`, `EMAIL_FROM` | SMTP URL and sender for magic-link sign-in (Mailpit locally: `smtp://localhost:1025`) |
| `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET` | Google OAuth |
| `AUTH_MICROSOFT_ENTRA_ID_ID`, `AUTH_MICROSOFT_ENTRA_ID_SECRET`, `AUTH_MICROSOFT_ENTRA_ID_ISSUER` | Microsoft Entra ID OAuth (issuer optional; omit for multi-tenant) |
| `BIDRADAR_DEV_TENANT_ID`, `BIDRADAR_DEV_ROLE` | Dev-only fallback for the `tenant_id` / `role` JWT claims until `resolveMembership()` calls the backend |
| `NEXT_PUBLIC_API_URL` | Backend base URL used by the API client (default `http://localhost:8000`) |
| `API_URL` | Spec source for `pnpm gen:api` when `../backend/openapi.json` is absent |
| `NEXT_PUBLIC_REGION` | `US` (default) or `IN`; shown as the region badge in the top bar |
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | _Optional._ RFC 8292 VAPID public key; the browser reads the key from `GET /api/v1/me/push-config` at runtime (M7-15) and only falls back to this build-time value when that route answers 404. Without either, the web-push button says push is not configured |

A provider whose env is missing is simply not registered, so `pnpm build` and
local dev work without any OAuth or SMTP credentials; the sign-in page shows
that method as disabled.

## Auth

- `src/auth.config.ts` – edge-safe config (JWT sessions, pages, callbacks) used
  by the middleware. The `jwt` callback puts `tenant_id` and `role` on the
  token via `resolveMembership(email)` (TODO: backend lookup; env fallback now).
- `src/auth.ts` – full config with Nodemailer, Google and Microsoft Entra ID
  providers, plus `getApiToken()`: signs a 15-minute HS256 JWT
  `{sub, email, tenant_id, role, iat, exp}` with `AUTH_SECRET` for the backend.
- `src/types/next-auth.d.ts` – `Session.user` / `JWT` type augmentation.
- `src/lib/auth-adapter.ts` – **dev-only in-memory adapter**. Auth.js requires
  an adapter for magic-link tokens even with JWT sessions; it is attached only
  when `EMAIL_SERVER`/`EMAIL_FROM` are set and forgets everything on restart.
  TODO(M1): replace with a backend-backed adapter (users live in the regional
  Postgres).

## API client

```sh
pnpm gen:api    # ../backend/openapi.json if present, else $API_URL/openapi.json
```

Writes `src/lib/api/schema.d.ts` (openapi-typescript). The committed file is a
hand-written placeholder covering only `GET /healthz` and is overwritten by the
script. `src/lib/api/client.ts` exports `api`, an `openapi-fetch` client with
base URL `NEXT_PUBLIC_API_URL` and a middleware that attaches the bearer token
from `getApiToken()` (server-side use).

```ts
import { api } from "@/lib/api/client";
const { data } = await api.GET("/healthz");
```

## Onboarding wizard (M1-13)

`/app/onboarding/region` (US or India) then `/app/onboarding?step=1..7`. The
wizard is client-rendered (`src/components/onboarding/`) and talks to the
backend through `src/app/api/v1/[...path]/route.ts`, a same-origin proxy that
attaches the session's bearer token, so browser code never sees `AUTH_SECRET`
and Playwright can stub `/api/v1/**` with `page.route`.

- `src/lib/profile-fields.ts` – the single field-metadata table (SPEC §4
  "Region" column): every step renders `fieldsForRegion(region, step)` and
  region-gated option lists, so a region-foreign field is never shown. A 422
  `region_mismatch` from the API is still surfaced inline with the field names.
- `src/lib/autofill.ts` – typed contract for `POST /profiles/{id}/autofill`
  (M1-10). Accepting a suggestion writes through the normal PUT/POST endpoints;
  a 404 shows a "not available" notice. Because the endpoint is profile-scoped,
  running autofill on step 1 creates the draft profile (legal name + region)
  first; otherwise the profile is created when step 1 is saved.
- `src/lib/completeness.ts` – badge logic (matching ≥ 40, drafting ≥ 70 and
  ≥ 3 past performances) and readable "missing" items; the meter re-reads
  `GET /profiles/{id}` after every step save.
- `src/lib/money.ts` – USD/INR formatting (INR in lakh/crore grouping).
- `src/lib/onboarding/api.ts` – typed wrappers plus `syncCollection`, which
  reconciles list editors with the 13 sub-resources (POST new, PUT changed,
  DELETE removed).

E2E: `e2e/onboarding.spec.ts` drives the full wizard against `e2e/mock-api.ts`
(fixtures in `e2e/fixtures/`). `e2e/auth.ts` mints an Auth.js session cookie
with the same `AUTH_SECRET` the Playwright web server is started with, so no
mail server or OAuth is needed. `pnpm e2e` skips with a message when Chromium
is not installed (`pnpm exec playwright install chromium`).

## Opportunity screens (M2-18)

- `/app/opportunities` — filter sidebar (query, region, notice type, NAICS,
  due before, status, minimum fit score), TanStack Table results (score,
  title, buyer, value, due, type, source) with server-side pagination. The
  filter state lives in the query string (`src/lib/opportunities/filters.ts`),
  so any URL is a saved search; the saved-search bar talks to
  `GET/POST /api/v1/saved-searches` (M4-08) and explains itself on 404.
- `/app/opportunities/[id]` — header with buyer breadcrumb and dual-zone
  dates, Pursue / Watch / Pass (with reason) posting to
  `/api/v1/opportunities/{id}/pursue|watch|pass` (M6-01; a 404 becomes a
  toast), summary, fit-score placeholder ready for `match`, eligibility
  checks, documents, versions diff, contacts, also-from links and the
  attribution footer with the SPEC 11 disclaimer.
- Dates: `src/lib/opportunities/dates.ts` mirrors the backend's
  `dual_tz` / `countdown` for both wire shapes (ISO string + `source_tz`, or
  the OQ-24 `TzDateOut` object): buyer's clock first, the viewer's second.
- E2E: `e2e/opportunities.spec.ts` runs against `e2e/mock-api.ts`, which
  serves `e2e/fixtures/opportunities.json` / `opportunity-detail.json` with
  the API's filter semantics (Chromium pinned to Asia/Kolkata).

## Home, alerts and notification settings (M4-16)

- `/app` — the dashboard: High-fit today (`GET /opportunities?min_score=70&status=open,closing_soon`),
  Due this week and Pipeline value by stage in USD and INR from
  `GET /dashboard` (the M6 aggregate; a 404 renders an empty state), and the
  alerts inbox of unread notifications with mark-read and deep links.
- Top bar bell — `src/components/notifications/notification-bell.tsx`: unread
  count polled every 60 s, the latest ten notifications, per-item and
  mark-all-read, the payload's `deep_link` rewritten onto this origin, and the
  signed one-click actions (Pursue / Watch / Pass with a reason / Assign).
- `/app/settings/notifications` — channels per event (8 events x 5 channels),
  quiet hours, IANA time zone with search, digest time, the instant and digest
  minimum scores (the backend's ordering rule is enforced in the form), the
  read-only email opt-out list and the web-push opt-in. The matrix reducer and
  validation live in `src/lib/notifications/prefs.ts`; the zone list and search
  in `src/lib/timezones.ts`.
- Web push: `public/sw.js` is a push-only service worker; the opt-in flow is
  `src/lib/notifications/push.ts`, and every unavailable state (no key, no
  browser support, permission denied) explains itself rather than failing.
- `/app/settings/saved-searches` — list, rename, delete, and per search the
  alert rule (mode instant/digest, minimum score, channels, enabled) against
  the M4-08 `/saved-searches` and `/alert-rules` contracts.
- Match feedback: thumbs up/down with a "Not relevant because…" dialog on the
  fit-score card and on every search row, posting to
  `POST /opportunities/{id}/feedback`.
- E2E: `e2e/dashboard.spec.ts` and `e2e/notifications.spec.ts` against
  `e2e/mock-api.ts` (`notifications.json`, `dashboard.json` fixtures; the
  `matches`, `dashboard`, `alertRules` and `feedback` options switch the
  not-yet-merged routes on).

## Tenant settings (M7-09)

`/app/settings` is a routed tab layout; the owner-only tabs are hidden for
other roles and their routes redirect (the API enforces it too).

- **Profile** — the completeness meter and a link into each of the seven
  wizard steps, which is where the company profile is edited.
- **Users & roles** (owner) — the member table with a role select, an invite
  dialog and all six SPEC 3 roles described. The endpoints
  (`GET /tenant/members`, `POST /tenant/members/invite`,
  `PATCH`/`DELETE /tenant/members/{id}`) are a contract: they answer 404 today
  and the page says so (OQ-92).
- **Integrations** (owner) — Slack, Teams, WhatsApp (Gupshup/Twilio + template
  names), Google and Microsoft calendar, each with an enabled toggle,
  `config` fields and write-only secrets shown as a "configured" indicator.
  `src/lib/settings/integrations.ts` holds the field table and makes the
  API's own checks first (https webhook, secrets *or* a `secret_ref`, never a
  secret inside `config`).
- **Billing** (owner) — plan, status, usage bars against `plan_limits`, the
  Free/Pro/Enterprise comparison from SPEC 3 and Upgrade →
  `POST /billing/checkout` → redirect to the provider. Razorpay tenants get
  GSTIN / place of supply / registered name. Plan arithmetic lives in
  `src/lib/settings/plans.ts`.
- **Data & privacy** — consent status per notice version, access / correction
  / erasure requests with their SLA date, and (owner only) tenant export and
  erasure behind a typed confirmation, plus the grievance officer and
  sub-processors from `GET /privacy`.
- E2E: `e2e/settings.spec.ts` with `e2e/fixtures/settings.json`.

## Scripts

| Script | What it does |
| --- | --- |
| `pnpm dev` / `build` / `start` | Next.js |
| `pnpm lint` | ESLint (next/core-web-vitals + typescript) |
| `pnpm typecheck` | `tsc --noEmit` |
| `pnpm test` | Vitest + Testing Library (jsdom); `src/**/*.test.tsx` |
| `pnpm e2e` | Playwright (`e2e/*.spec.ts`) on port 3100; skips gracefully until `pnpm exec playwright install chromium` has run |
| `pnpm gen:api` | Regenerate the API types |

## Adding UI components

```sh
pnpm dlx shadcn@latest add <component>
```

Components live in `src/components/ui/`. Installed: button, card, input,
label, badge, dropdown-menu, table, dialog, sonner, plus hand-written
textarea, native-select, checkbox and progress. Forms use react-hook-form +
zod (`@hookform/resolvers`). `@tanstack/react-table`
and `@tiptap/react` + `@tiptap/starter-kit` are installed for later
milestones.
