import { describe, expect, it } from "vitest";

import {
  canApprovePackage,
  canApproveSection,
  canComment,
  canDecide,
  canEditDrafts,
  canEditTasks,
  canExport,
  canMarkFinal,
  canReadDrafts,
  canRunAgents,
  gate1ApproverRoles,
  isReadOnly,
} from "@/lib/pursuits/roles";
import type { Role } from "@/types/next-auth";

const ROLES: Role[] = ["tenant_owner", "bid_manager", "writer", "reviewer", "viewer"];

const allowed = (predicate: (role: Role | undefined) => boolean) =>
  ROLES.filter((role) => predicate(role));

describe("workspace role gating (SPEC 3)", () => {
  it("keeps draft text away from the read-only viewer", () => {
    expect(allowed(canReadDrafts)).toEqual(["tenant_owner", "bid_manager", "writer", "reviewer"]);
    expect(canReadDrafts("viewer")).toBe(false);
  });

  it("lets the writer/SME edit drafts and the reviewer only approve them", () => {
    expect(allowed(canEditDrafts)).toEqual(["tenant_owner", "bid_manager", "writer"]);
    expect(allowed(canApproveSection)).toEqual(["tenant_owner", "bid_manager", "reviewer"]);
    expect(canEditDrafts("reviewer")).toBe(false);
    expect(canApproveSection("writer")).toBe(false);
  });

  it("keeps the agent run, the budget and both gates with the bid manager and owner", () => {
    for (const predicate of [canRunAgents, canApprovePackage, canMarkFinal]) {
      expect(allowed(predicate)).toEqual(["tenant_owner", "bid_manager"]);
    }
  });

  it("lets everyone but the viewer comment and download an export", () => {
    expect(allowed(canComment)).toEqual(["tenant_owner", "bid_manager", "writer", "reviewer"]);
    expect(allowed(canExport)).toEqual(["tenant_owner", "bid_manager", "writer", "reviewer"]);
  });

  it("makes a task editable from writer up (OQ-118 leaves closing to everyone)", () => {
    expect(allowed(canEditTasks)).toEqual(["tenant_owner", "bid_manager", "writer"]);
  });

  it("treats the viewer, and an unknown role, as read-only everywhere", () => {
    expect(isReadOnly("viewer")).toBe(true);
    expect(isReadOnly(undefined)).toBe(true);
    expect(isReadOnly("reviewer")).toBe(false);
    expect(isReadOnly("writer")).toBe(false);
  });

  describe("Gate 1 approvers come from the profile", () => {
    it("is the owner plus whatever required_approver_roles names", () => {
      expect(gate1ApproverRoles({ required_approver_roles: ["bid_manager", "reviewer"] })).toEqual([
        "tenant_owner",
        "bid_manager",
        "reviewer",
      ]);
      expect(canDecide("reviewer", { required_approver_roles: ["reviewer"] })).toBe(true);
      expect(canDecide("writer", { required_approver_roles: ["reviewer"] })).toBe(false);
    });

    it("is the owner alone when the profile configures nobody", () => {
      expect(gate1ApproverRoles({ required_approver_roles: [] })).toEqual(["tenant_owner"]);
      expect(canDecide("bid_manager", { required_approver_roles: [] })).toBe(false);
    });

    it("never lists the owner twice", () => {
      expect(gate1ApproverRoles({ required_approver_roles: ["tenant_owner"] })).toEqual(["tenant_owner"]);
    });

    it("falls back to SPEC 3 (owner + bid manager) with no profile loaded", () => {
      expect(gate1ApproverRoles(null)).toEqual(["tenant_owner", "bid_manager"]);
      expect(canDecide("bid_manager")).toBe(true);
      expect(canDecide("viewer")).toBe(false);
    });
  });
});
