import { describe, expect, it } from "vitest";

import {
  PLATFORM_ADMIN_META,
  ROLE_META,
  TENANT_ROLES,
  canManageBilling,
  canManageMembers,
  canManageTenantData,
  isLastOwner,
  isTenantRole,
  roleDescription,
  roleLabel,
} from "./roles";

describe("the SPEC 3 roles", () => {
  it("offers the five tenant roles, in authority order, and not platform_admin", () => {
    expect(ROLE_META.map((meta) => meta.value)).toEqual([...TENANT_ROLES]);
    expect(TENANT_ROLES).not.toContain("platform_admin" as never);
    expect(isTenantRole("bid_manager")).toBe(true);
    expect(isTenantRole("platform_admin")).toBe(false);
  });

  it("describes all six roles, ours included", () => {
    for (const meta of [...ROLE_META, PLATFORM_ADMIN_META]) {
      expect(roleLabel(meta.value)).toBe(meta.label);
      expect(roleDescription(meta.value).length).toBeGreaterThan(10);
    }
  });

  it("falls back to a readable label for an unknown role", () => {
    expect(roleLabel("data_steward")).toBe("data steward");
    expect(roleDescription("data_steward")).toBe("");
  });
});

describe("who may administer the tenant", () => {
  it("is the owner, and only the owner", () => {
    expect(canManageMembers("tenant_owner")).toBe(true);
    expect(canManageBilling("tenant_owner")).toBe(true);
    expect(canManageTenantData("tenant_owner")).toBe(true);
    for (const role of ["bid_manager", "writer", "reviewer", "viewer", "platform_admin"] as const) {
      expect(canManageMembers(role), role).toBe(false);
      expect(canManageBilling(role), role).toBe(false);
    }
    expect(canManageMembers(undefined)).toBe(false);
  });
});

describe("isLastOwner", () => {
  const members = [
    { id: "m1", role: "tenant_owner" },
    { id: "m2", role: "bid_manager" },
  ];

  it("protects the only owner from being demoted", () => {
    expect(isLastOwner(members, "m1")).toBe(true);
    expect(isLastOwner(members, "m2")).toBe(false);
  });

  it("lets an owner change once there are two", () => {
    const two = [...members, { id: "m3", role: "tenant_owner" }];
    expect(isLastOwner(two, "m1")).toBe(false);
    expect(isLastOwner(two, "m3")).toBe(false);
  });
});
