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
