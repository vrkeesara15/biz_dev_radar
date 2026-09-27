/**
 * Typed wrappers over the platform-admin routes (SPEC 10.3, screen 10.4.9).
 *
 * Every call goes through the same-origin proxy (`src/app/api/v1/[...path]`)
 * with the session's bearer token, so a non-platform_admin simply gets the
 * backend's 403 and the console says so rather than hiding the failure.
 */
import { browserApi, type Schemas } from "@/lib/api/browser";

export type TenantRow = Schemas["TenantRowOut"];
export type TenantPage = Schemas["TenantPage"];
export type TenantDetail = Schemas["TenantDetailOut"];
export type UsagePage = Schemas["UsagePage"];
export type UsageRow = Schemas["UsageRowOut"];
export type AdminSource = Schemas["SourceOut"];
export type SourceRun = Schemas["SourceRunOut"];
export type SourceRunPage = Schemas["SourceRunPage"];
export type RunSourceResult = Schemas["RunSourceOut"];
export type SystemHealth = Schemas["HealthOut"];
export type AdapterHealth = Schemas["AdapterHealthOut"];
export type SupportAccess = Schemas["SupportAccessOut"];
export type AuditLogPage = Schemas["AuditLogPage"];
export type Plan = Schemas["Plan"];

/** Non-2xx from an admin route; `status` drives the 403 / "not available" copy. */
export class AdminApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `Request failed (${status})`);
    this.name = "AdminApiError";
    this.status = status;
    this.body = body;
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new AdminApiError(response.status, error);
  return data as T;
}

export type TenantQuery = {
  q?: string;
  page?: number;
  page_size?: number;
  include_deleted?: boolean;
};

export const listTenants = (query: TenantQuery = {}, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/admin/tenants", {
      params: { query: query as never },
      signal,
    }),
  );

export const getTenant = (tenantId: string, period?: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/admin/tenants/{tenant_id}", {
      params: { path: { tenant_id: tenantId }, query: (period ? { period } : {}) as never },
      signal,
    }),
  );

export const updateTenant = (
  tenantId: string,
  body: { plan?: Plan; is_internal?: boolean },
) =>
  unwrap(
    browserApi.PATCH("/api/v1/admin/tenants/{tenant_id}", {
      params: { path: { tenant_id: tenantId } },
      body,
    }),
  );

export const grantSupportAccess = (
  tenantId: string,
  body: { reason: string; minutes?: number | null },
) =>
  unwrap(
    browserApi.POST("/api/v1/admin/tenants/{tenant_id}/support-access", {
      params: { path: { tenant_id: tenantId } },
      body: { reason: body.reason, minutes: body.minutes ?? null },
    }),
  );

export const getTenantAuditLog = (tenantId: string, page = 1, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/admin/tenants/{tenant_id}/audit-log", {
      params: { path: { tenant_id: tenantId }, query: { page } as never },
      signal,
    }),
  );

export const listSources = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/admin/sources", { signal }));

export const listSourceRuns = (
  sourceId: string,
  query: { page?: number; page_size?: number } = {},
  signal?: AbortSignal,
) =>
  unwrap(
    browserApi.GET("/api/v1/admin/sources/{source_id}/runs", {
      params: { path: { source_id: sourceId }, query: query as never },
      signal,
    }),
  );

export const runSourceNow = (sourceId: string, inline = false) =>
  unwrap(
    browserApi.POST("/api/v1/admin/sources/{source_id}/run", {
      params: { path: { source_id: sourceId } },
      body: { inline },
    }),
  );

export const getUsage = (period?: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/admin/usage", {
      params: { query: (period ? { period } : {}) as never },
      signal,
    }),
  );

export const getSystemHealth = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/admin/health", { signal }));
