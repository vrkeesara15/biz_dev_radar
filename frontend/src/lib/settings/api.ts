/**
 * Typed wrappers for the tenant settings screens (SPEC 10.4 screen 8).
 *
 * Integrations, billing, consents, data requests, the public privacy page and —
 * since M7-15 — tenant members are all in the generated schema, so every call
 * below is typed from the backend's OpenAPI spec. A 404 still surfaces as
 * `NotAvailableError` so a screen can say "not on this deployment yet".
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

// --- members (M7-15) ----------------------------------------------------------

/** One row of GET /api/v1/tenant/members; `id` is the membership id. */
export type Member = Schemas["MemberOut"];
export type MemberInvite = Schemas["MemberInviteIn"];

/** Shown when a deployment predates the member routes (they answer 404 there). */
export const MEMBERS_UNAVAILABLE_MESSAGE =
  "Member management arrives with the tenant users endpoint";

export const listMembers = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/tenant/members", { signal }));

export const inviteMember = (body: MemberInvite) =>
  unwrap(browserApi.POST("/api/v1/tenant/members/invite", { body }));

export const updateMemberRole = (membershipId: string, role: Member["role"]) =>
  unwrap(
    browserApi.PATCH("/api/v1/tenant/members/{membership_id}", {
      params: { path: { membership_id: membershipId } },
      body: { role },
    }),
  );

export const removeMember = (membershipId: string) =>
  unwrap(
    browserApi.DELETE("/api/v1/tenant/members/{membership_id}", {
      params: { path: { membership_id: membershipId } },
    }),
  );
