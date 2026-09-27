/**
 * Typed wrappers for the tenant settings screens (SPEC 10.4 screen 8).
 *
 * Integrations, billing, consents, data requests and the public privacy page
 * are in the generated schema. Member management is NOT: M0-07 provisions a
 * membership just in time and there is no invite or role route on main yet, so
 * those four calls go through the same-origin proxy with plain fetch against
 * the contract below and surface `NotAvailableError` on 404 (OQ-92).
 */
import { browserApi, type Schemas } from "@/lib/api/browser";
import { ApiError, NotAvailableError } from "@/lib/opportunities/api";

export type Integration = Schemas["IntegrationOut"];
export type IntegrationKind = Schemas["IntegrationKind"];
export type IntegrationIn = Schemas["IntegrationIn"];
export type Billing = Schemas["BillingOut"];
export type Checkout = Schemas["CheckoutOut"];
export type CheckoutIn = Schemas["CheckoutIn"];
export type Consent = Schemas["ConsentOut"];
export type ConsentKind = Schemas["ConsentKind"];
export type DataRequest = Schemas["DataRequestOut"];
export type DataRequestKind = Schemas["DataRequestKind"];
export type TenantJob = Schemas["TenantJobOut"];
export type Privacy = Schemas["PrivacyOut"];
export type Me = Schemas["MeOut"];

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) {
    if (response.status === 404) throw new NotAvailableError(error);
    throw new ApiError(response.status, error);
  }
  return data as T;
}

export const getMe = (signal?: AbortSignal) => unwrap(browserApi.GET("/api/v1/me", { signal }));

// --- integrations -------------------------------------------------------------

export const listIntegrations = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/integrations", { signal }));

export const putIntegration = (kind: IntegrationKind, body: IntegrationIn) =>
  unwrap(
    browserApi.PUT("/api/v1/integrations/{kind}", {
      params: { path: { kind } },
      body,
    }),
  );

// --- billing ------------------------------------------------------------------

export const getBilling = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/billing", { signal }));

export const startCheckout = (body: CheckoutIn) =>
  unwrap(browserApi.POST("/api/v1/billing/checkout", { body }));

// --- privacy ------------------------------------------------------------------

export const getPrivacy = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/privacy", { signal }));

export const listConsents = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/me/consents", { signal }));

export const recordConsent = (body: Schemas["ConsentIn"]) =>
  unwrap(browserApi.POST("/api/v1/me/consents", { body }));

export const listDataRequests = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/me/data-requests", { signal }));

export const openDataRequest = (body: Schemas["DataRequestIn"]) =>
  unwrap(browserApi.POST("/api/v1/me/data-requests", { body }));

export const exportTenant = () => unwrap(browserApi.POST("/api/v1/tenant/export", {}));

export const deleteTenant = () => unwrap(browserApi.POST("/api/v1/tenant/delete", {}));

// --- members (contract; not on main yet — see OQ-92) ---------------------------

/** One row of GET /api/v1/tenant/members. */
export type Member = {
  id: string;
  user_id: string;
  email: string;
  name: string | null;
  role: string;
  /** "active" once the person has signed in, "invited" before that. */
  status?: string | null;
  invited_at?: string | null;
  last_seen_at?: string | null;
};

export const MEMBERS_UNAVAILABLE_MESSAGE =
  "Member management arrives with the tenant users endpoint";

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function jsonRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
    cache: "no-store",
  });
  const body = await readBody(response);
  if (response.status === 404) throw new NotAvailableError(body);
  if (!response.ok) throw new ApiError(response.status, body);
  return body as T;
}

export const listMembers = () => jsonRequest<Member[]>("/api/v1/tenant/members");

export const inviteMember = (body: { email: string; role: string }) =>
  jsonRequest<Member>("/api/v1/tenant/members/invite", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateMemberRole = (memberId: string, role: string) =>
  jsonRequest<Member>(`/api/v1/tenant/members/${encodeURIComponent(memberId)}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });

export const removeMember = (memberId: string) =>
  jsonRequest<unknown>(`/api/v1/tenant/members/${encodeURIComponent(memberId)}`, {
    method: "DELETE",
  });
