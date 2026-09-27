/**
 * Tenant roles (SPEC 3), mirroring `backend/app/core/roles.py`.
 *
 * Pure: the descriptions shown in the invite dialog and the role select, the
 * rank used to order them, and the two questions the settings screens ask —
 * may this role manage members, and may it be assigned to someone.
 */
import type { Role } from "@/types/next-auth";

export type RoleMeta = { value: TenantRole; label: string; description: string };

/** platform_admin is ours, never a tenant membership, so it is not assignable. */
export const TENANT_ROLES = [
  "tenant_owner",
  "bid_manager",
  "writer",
  "reviewer",
  "viewer",
] as const;
export type TenantRole = (typeof TENANT_ROLES)[number];

export const ROLE_META: readonly RoleMeta[] = [
  {
    value: "tenant_owner",
    label: "Tenant owner",
    description: "Billing, users, company profiles, integrations and data retention.",
  },
  {
    value: "bid_manager",
    label: "Bid manager",
    description:
      "Configures searches and alert rules, moves pursuits through stages, assigns owners, approves bid/no-bid and the final package.",
  },
  {
    value: "writer",
    label: "Writer / SME",
    description: "Edits drafts, answers agent questions, uploads past performance and resumes.",
  },
  { value: "reviewer", label: "Reviewer", description: "Comments on and approves sections only." },
  { value: "viewer", label: "Viewer", description: "Read-only dashboards." },
] as const;

/** The sixth SPEC 3 role: ours, listed for completeness, never assignable here. */
export const PLATFORM_ADMIN_META: { value: Role; label: string; description: string } = {
  value: "platform_admin",
  label: "Platform admin",
  description:
    "BidRadar staff: tenants, plans, source adapters and system health. No access to your drafts unless you grant support access, which is logged.",
};

export function roleLabel(role: string): string {
  if (role === PLATFORM_ADMIN_META.value) return PLATFORM_ADMIN_META.label;
  return ROLE_META.find((meta) => meta.value === role)?.label ?? role.replace(/_/g, " ");
}

export function roleDescription(role: string): string {
  if (role === PLATFORM_ADMIN_META.value) return PLATFORM_ADMIN_META.description;
  return ROLE_META.find((meta) => meta.value === role)?.description ?? "";
}

export const isTenantRole = (role: string): role is TenantRole =>
  (TENANT_ROLES as readonly string[]).includes(role);

/** SPEC 3: only the tenant owner manages members, billing and integrations. */
export const canManageMembers = (role: Role | undefined) => role === "tenant_owner";
export const canManageBilling = canManageMembers;
export const canManageIntegrations = canManageMembers;
/** Tenant-wide export and erasure are the owner's; a personal request is not. */
export const canManageTenantData = canManageMembers;

/**
 * An owner may not demote themselves while they are the only one left, or the
 * tenant loses its billing and user administration.
 */
export function isLastOwner(members: { id: string; role: string }[], memberId: string): boolean {
  const owners = members.filter((member) => member.role === "tenant_owner");
  return owners.length === 1 && owners[0].id === memberId;
}
