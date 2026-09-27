/**
 * Browser-side typed API client.
 *
 * Uses an empty base URL so every path in the generated schema resolves to the
 * same origin, where `src/app/api/v1/[...path]/route.ts` proxies it to the
 * backend with the session's bearer token. Client components import this file;
 * server code keeps using `@/lib/api/client`.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

// The base URL is the page origin (the Request constructor needs an absolute
// URL); `fetch` is resolved lazily so tests can stub `globalThis.fetch`.
export const browserApi = createClient<paths>({
  baseUrl: typeof window !== "undefined" ? window.location.origin : "http://localhost",
  fetch: (input) => globalThis.fetch(input),
});

export type Schemas = components["schemas"];
export type Profile = Schemas["ProfileOut"];
export type ProfileCreate = Schemas["ProfileCreate"];
export type ProfileUpdate = Schemas["ProfileUpdate"];
export type Completeness = Schemas["CompletenessOut"];
export type ApiRegion = Schemas["Region"];

/** Body of a 422 raised when a payload carries fields of the other region. */
export type RegionMismatch = {
  error: "region_mismatch";
  region: string;
  fields: string[];
  message?: string;
};

/** Backend wraps HTTPException payloads in `detail`; normalise both shapes. */
export function asRegionMismatch(body: unknown): RegionMismatch | null {
  if (!body || typeof body !== "object") return null;
  const candidate =
    "detail" in body && body.detail && typeof body.detail === "object"
      ? (body.detail as Record<string, unknown>)
      : (body as Record<string, unknown>);
  if (candidate.error === "region_mismatch" && Array.isArray(candidate.fields)) {
    return {
      error: "region_mismatch",
      region: String(candidate.region ?? ""),
      fields: candidate.fields.map(String),
      message: typeof candidate.message === "string" ? candidate.message : undefined,
    };
  }
  return null;
}

/** Human-readable message from an error body (FastAPI `detail` or plain). */
export function errorMessage(body: unknown, fallback = "Request failed"): string {
  if (!body || typeof body !== "object") return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.slice(1).join(".") : "";
        return loc ? `${loc}: ${d.msg}` : String(d?.msg ?? "");
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return fallback;
}
