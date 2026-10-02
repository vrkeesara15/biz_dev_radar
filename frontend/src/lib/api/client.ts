/**
 * Typed API client (openapi-fetch) over the generated OpenAPI schema.
 *
 * Server-side only: the auth middleware calls `getApiToken()` which reads the
 * Auth.js session from request cookies and signs a short-lived HS256 JWT that
 * the backend verifies with the shared AUTH_SECRET.
 */
import createClient, { type Middleware } from "openapi-fetch";

import { getApiToken } from "@/auth";

import type { paths } from "./schema";

/**
 * Base URL of the backend, resolved at RUNTIME where possible (OQ-77).
 *
 * Next inlines `NEXT_PUBLIC_*` into the bundle at build time, so an image built once
 * and promoted to several environments carried the build machine's API URL — the one
 * place the "same image everywhere" rule leaked. Nothing in the browser needs this
 * value (client components go through the same-origin proxy at
 * `src/app/api/v1/[...path]/route.ts`), so the server reads a plain `API_URL`
 * instead, which a platform can set per environment without a rebuild.
 *
 * Order: `API_URL` (runtime, wins) → `NEXT_PUBLIC_API_URL` (baked at build, the
 * fallback) → localhost.
 */
export function resolveApiBaseUrl(): string {
  return (
    process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
  );
}

/** Module-load snapshot of {@link resolveApiBaseUrl}. The proxy route calls the
 * function per request so a variable changed on a running server takes effect. */
export const API_BASE_URL = resolveApiBaseUrl();

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = await getApiToken();
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: API_BASE_URL });
api.use(authMiddleware);

export type { paths, components } from "./schema";
