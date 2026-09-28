/**
 * What each SPEC 3 role may do in the pursuit workspace (M5-18).
 *
 * Pure, and deliberately a mirror of the backend's dependency tuples rather
 * than a guess:
 *
 *   drafts.py    READ_ROLES    owner, bid manager, writer, reviewer   (no viewer)
 *                WRITE_ROLES   owner, bid manager, writer
 *                APPROVE_ROLES owner, bid manager, reviewer
 *   exports.py   READ_ROLES    owner, bid manager, writer, reviewer   (no viewer)
 *                MANAGER_ROLES owner, bid manager                     (mark final)
 *   pursuits.py  MANAGER_ROLES owner, bid manager   (run agents, budget, Gate 2)
 *   collab.py    COMMENT_ROLES owner, bid manager, writer, reviewer
 *                WRITER_ROLES  owner, bid manager, writer  (create/edit a task)
 *                every tenant role may close a task and read the pursuit
 *
 * The server is always the authority; these helpers only decide whether a
 * control is drawn, so a viewer is never shown a button that would 403.
 *
 * Gate 1 is the exception: `POST /pursuits/{id}/decision` allows the tenant
 * owner plus whatever the profile's `required_approver_roles` names
 * (services.pursuits.approver_roles), so it is answered by
 * `gate1ApproverRoles(profile)` with the profile loaded, and by SPEC 3's
 * "bid manager approves bid/no-bid" when it is not (OQ-149).
 */
import type { Role } from "@/types/next-auth";

/** SPEC 3 roles that are a tenant membership (platform_admin is ours). */
export const OWNER: Role = "tenant_owner";
export const MANAGER: Role = "bid_manager";
export const WRITER: Role = "writer";
export const REVIEWER: Role = "reviewer";
export const VIEWER: Role = "viewer";

const has = (roles: readonly Role[], role: Role | undefined | null) =>
  !!role && roles.includes(role);

/** owner + bid manager: stage moves, agent runs, budget, Gate 2, mark final. */
export const MANAGER_ROLES: readonly Role[] = [OWNER, MANAGER];
/** Everyone but the viewer: draft text, exports and comments. */
export const DRAFT_READ_ROLES: readonly Role[] = [OWNER, MANAGER, WRITER, REVIEWER];
/** Who may edit a draft body (SPEC 3 writer row). */
export const DRAFT_WRITE_ROLES: readonly Role[] = [OWNER, MANAGER, WRITER];
/** Who may approve one section (SPEC 3 reviewer row). */
export const SECTION_APPROVE_ROLES: readonly Role[] = [OWNER, MANAGER, REVIEWER];

/** Read the pursuit, the matrix, the packet, the checklist and the task list. */
export const canReadPursuit = (role: Role | undefined | null) => !!role;
/** SPEC 3: a viewer gets read-only dashboards and never sees draft text. */
export const canReadDrafts = (role: Role | undefined | null) => has(DRAFT_READ_ROLES, role);
export const canEditDrafts = (role: Role | undefined | null) => has(DRAFT_WRITE_ROLES, role);
export const canApproveSection = (role: Role | undefined | null) =>
  has(SECTION_APPROVE_ROLES, role);
export const canComment = (role: Role | undefined | null) => has(DRAFT_READ_ROLES, role);
/** A viewer never downloads a proposal export either (exports.py READ_ROLES). */
export const canExport = (role: Role | undefined | null) => has(DRAFT_READ_ROLES, role);
export const canRunAgents = (role: Role | undefined | null) => has(MANAGER_ROLES, role);
export const canApproveBudget = canRunAgents;
/** Gate 2: approve the package, and then mark it final. */
export const canApprovePackage = canRunAgents;
export const canMarkFinal = canRunAgents;
/** Creating, reassigning and re-dating a task is writer-and-up (OQ-118). */
export const canEditTasks = (role: Role | undefined | null) => has(DRAFT_WRITE_ROLES, role);

/**
 * Gate 1 approvers: the tenant owner always, plus the profile's configured
 * `required_approver_roles`. With no profile loaded we fall back to SPEC 3's
 * "bid manager ... approve bid/no-bid" so the control is not hidden from the
 * role the spec names; the route still decides.
 */
export function gate1ApproverRoles(
  profile: { required_approver_roles?: readonly string[] | null } | null | undefined,
): readonly Role[] {
  if (!profile) return MANAGER_ROLES;
  const configured = (profile.required_approver_roles ?? []).filter(
    (name): name is Role => typeof name === "string",
  );
  return [OWNER, ...configured.filter((name) => name !== OWNER)];
}

export const canDecide = (
  role: Role | undefined | null,
  profile?: { required_approver_roles?: readonly string[] | null } | null,
) => has(gate1ApproverRoles(profile), role);

/** True when the role may change nothing at all in the workspace (SPEC 3 viewer). */
export const isReadOnly = (role: Role | undefined | null) =>
  !canEditDrafts(role) && !canApproveSection(role) && !canComment(role) && !canRunAgents(role);
