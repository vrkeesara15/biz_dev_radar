/**
 * Pure workspace logic (M5-18): the SPEC 10.4 tab set, the agent steps the
 * "Run agents" control offers, the cost meter, the bid/no-bid scorecard
 * reader and the Activity timeline composed from what the API does expose.
 */
import type { PursuitComment, PursuitOut, PursuitTask } from "@/lib/pursuits/api";
import type { DraftSummary, Export, Run } from "@/lib/pursuits/workspace-api";

// --- tabs (SPEC 10.4 screen 6) -----------------------------------------------

export const TABS = [
  { id: "bid-no-bid", label: "Bid/no-bid" },
  { id: "matrix", label: "Compliance matrix" },
  { id: "drafts", label: "Drafts" },
  { id: "pricing", label: "Pricing" },
  { id: "checklist", label: "Checklist" },
  { id: "tasks", label: "Tasks" },
  { id: "activity", label: "Activity" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export const isTabId = (value: unknown): value is TabId =>
  typeof value === "string" && TABS.some((tab) => tab.id === value);

export const DEFAULT_TAB: TabId = "bid-no-bid";

// --- agent steps (app.agents.pipeline.PIPELINE_ORDER) -------------------------

export const AGENT_STEPS = [
  { id: "collect", label: "1. Collect documents" },
  { id: "extract", label: "2. Extract requirements" },
  { id: "matrix", label: "3. Compliance matrix" },
  { id: "bid_no_bid", label: "4. Bid/no-bid (Gate 1)" },
  { id: "outline", label: "5. Outline" },
  { id: "draft", label: "6. Draft sections" },
  { id: "pricing", label: "7. Pricing" },
  { id: "red_team", label: "8. Red team (Gate 2)" },
] as const;

export type AgentStep = (typeof AGENT_STEPS)[number]["id"] | "all";

export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  needs_approval: "Needs budget approval",
  failed: "Failed",
  done: "Done",
};

export const GATE_LABELS: Record<string, string> = {
  gate1: "Gate 1: bid/no-bid decision",
  gate2: "Gate 2: package review and approval",
};

export const runStatusLabel = (status: string | null | undefined) =>
  (status && RUN_STATUS_LABELS[status]) || status || "No run yet";

export const gateLabel = (gate: string | null | undefined) =>
  (gate && GATE_LABELS[gate]) || gate || null;

/** The cost guard stopped and is asking for more budget (SPEC 8 cost guard). */
export const needsBudgetApproval = (run: Run | null | undefined) =>
  run?.status === "needs_approval";

// --- cost meter ----------------------------------------------------------------

export type CostMeter = {
  spent: number;
  cap: number;
  /** 0-100, clamped; 100 when the cap is 0 and anything was spent. */
  percent: number;
  over: boolean;
  /** Within 10% of the cap (or past it): the meter turns amber/red. */
  near: boolean;
  monthRemaining: number | null;
};

const num = (value: string | number | null | undefined): number => {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === "number" ? value : Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

export function costMeter(pursuit: PursuitOut): CostMeter {
  const spent = num(pursuit.cost_so_far_usd);
  const cap = num(pursuit.cost_cap_usd);
  const percent = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : spent > 0 ? 100 : 0;
  const remaining =
    pursuit.budget_month_remaining_usd === null || pursuit.budget_month_remaining_usd === undefined
      ? null
      : num(pursuit.budget_month_remaining_usd);
  return {
    spent,
    cap,
    percent,
    over: cap > 0 && spent >= cap,
    near: percent >= 90,
    monthRemaining: remaining,
  };
}

export const usd = (amount: number) =>
  `$${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// --- the bid/no-bid scorecard (agent 4) ------------------------------------------

export type ScorecardGap = { gap: string; suggested_fix: string };
export type ScorecardTeaming = { partner_or_capability: string; why: string };

export type Scorecard = {
  fit: number;
  eligibility: number;
  capacity: number;
  competition: number;
  value_fit: number;
  win_probability: number;
  incumbent_note: string | null;
  gaps: ScorecardGap[];
  teaming_suggestions: ScorecardTeaming[];
  recommendation: string;
  reasons: string[];
  /** From the enclosing ScorecardOutput when it is there. */
  weighted_score: string | null;
  suggested_recommendation: string | null;
  version: number | null;
};

/** The six criteria SPEC 8 names, in the order the panel draws them. */
export const SCORECARD_CRITERIA: { key: keyof Scorecard & string; label: string }[] = [
  { key: "fit", label: "Fit" },
  { key: "eligibility", label: "Eligibility" },
  { key: "capacity", label: "Capacity" },
  { key: "competition", label: "Competition" },
  { key: "value_fit", label: "Value" },
  { key: "win_probability", label: "Win probability" },
];

const record = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

const score = (value: unknown): number => {
  const parsed = typeof value === "number" ? value : Number.parseFloat(String(value ?? ""));
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(100, Math.round(parsed)));
};

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];

/**
 * A Scorecard out of whatever an artifact route hands back: the
 * `ScorecardOutput` itself, an artifact row wrapping it in `data`, or a list
 * of rows (newest first). Returns null when nothing in there looks like one.
 */
export function readScorecard(payload: unknown): Scorecard | null {
  const envelope = record(payload);
  if (!envelope) {
    if (Array.isArray(payload)) {
      for (const item of payload) {
        const found = readScorecard(item);
        if (found) return found;
      }
    }
    return null;
  }
  if (Array.isArray(envelope.items)) {
    for (const item of envelope.items) {
      const found = readScorecard(item);
      if (found) return found;
    }
  }
  const output = record(envelope.data) ?? envelope;
  const card = record(output.scorecard);
  if (!card) return null;
  const version = output.version ?? envelope.version;
  return {
    fit: score(card.fit),
    eligibility: score(card.eligibility),
    capacity: score(card.capacity),
    competition: score(card.competition),
    value_fit: score(card.value_fit),
    win_probability: score(card.win_probability),
    incumbent_note: typeof card.incumbent_note === "string" ? card.incumbent_note : null,
    gaps: (Array.isArray(card.gaps) ? card.gaps : [])
      .map((row) => record(row))
      .filter((row): row is Record<string, unknown> => !!row)
      .map((row) => ({ gap: String(row.gap ?? ""), suggested_fix: String(row.suggested_fix ?? "") }))
      .filter((row) => row.gap),
    teaming_suggestions: (Array.isArray(card.teaming_suggestions) ? card.teaming_suggestions : [])
      .map((row) => record(row))
      .filter((row): row is Record<string, unknown> => !!row)
      .map((row) => ({
        partner_or_capability: String(row.partner_or_capability ?? ""),
        why: String(row.why ?? ""),
      }))
      .filter((row) => row.partner_or_capability),
    recommendation: String(card.recommendation ?? "unknown"),
    reasons: strings(card.reasons),
    weighted_score: output.weighted_score === undefined ? null : String(output.weighted_score),
    suggested_recommendation:
      typeof output.suggested_recommendation === "string" ? output.suggested_recommendation : null,
    version: typeof version === "number" ? version : null,
  };
}

// --- activity timeline -----------------------------------------------------------

export type ActivityEvent = {
  id: string;
  at: string;
  kind: "decision" | "approval" | "run" | "draft" | "task" | "comment" | "export" | "pursuit";
  title: string;
  detail: string | null;
  /** The user behind it, when the record names one. */
  userId: string | null;
};

export type ActivityInput = {
  pursuit: PursuitOut;
  drafts?: readonly DraftSummary[];
  tasks?: readonly PursuitTask[];
  comments?: readonly PursuitComment[];
  exports?: readonly Export[];
};

/**
 * SPEC 10.4's Activity tab, composed rather than read: there is no activity
 * or audit-log route for a tenant user (`GET /admin/audit` is the platform
 * admin's), so the timeline is built from the timestamps the pursuit, its
 * drafts, tasks, comments and exports already carry (OQ-150). Newest first.
 */
export function activityTimeline(input: ActivityInput): ActivityEvent[] {
  const { pursuit } = input;
  const events: ActivityEvent[] = [];
  const push = (event: ActivityEvent | null) => {
    if (event && event.at) events.push(event);
  };

  push({
    id: `pursuit-created-${pursuit.id}`,
    at: pursuit.created_at,
    kind: "pursuit",
    title: "Pursuit created",
    detail: null,
    userId: pursuit.created_by ?? null,
  });
  if (pursuit.decided_at) {
    push({
      id: `decision-${pursuit.id}`,
      at: pursuit.decided_at,
      kind: "decision",
      title: pursuit.decision === "bid" ? "Gate 1: bid" : "Gate 1: no-bid",
      detail: pursuit.decision_note ?? null,
      userId: pursuit.decided_by ?? null,
    });
  }
  if (pursuit.package_approved_at) {
    push({
      id: `package-${pursuit.id}`,
      at: pursuit.package_approved_at,
      kind: "approval",
      title: "Gate 2: package approved",
      detail: null,
      userId: pursuit.package_approved_by ?? null,
    });
  }
  if (pursuit.submitted_at) {
    push({
      id: `submitted-${pursuit.id}`,
      at: pursuit.submitted_at,
      kind: "pursuit",
      title: "Marked submitted",
      detail: null,
      userId: null,
    });
  }
  const run = pursuit.run;
  if (run) {
    push({
      id: `run-${run.id}`,
      at: run.finished_at || run.started_at || run.created_at,
      kind: "run",
      title: `Agent run ${runStatusLabel(run.status).toLowerCase()}${run.step ? ` (${run.step})` : ""}`,
      detail: run.pause_reason ?? gateLabel(run.gate),
      userId: null,
    });
  }
  for (const draft of input.drafts ?? []) {
    push({
      id: `draft-${draft.id}`,
      at: draft.updated_at,
      kind: "draft",
      title: `${draft.title} — version ${draft.version ?? 1} (${draft.status.replace(/_/g, " ")})`,
      detail:
        draft.unsupported_claims || draft.needs_input
          ? `${draft.unsupported_claims ?? 0} unsupported, ${draft.needs_input ?? 0} needs input`
          : null,
      userId: null,
    });
  }
  for (const task of input.tasks ?? []) {
    push({
      id: `task-${task.id}`,
      at: task.completed_at || task.created_at,
      kind: "task",
      title: task.completed_at ? `Task done: ${task.title}` : `Task opened: ${task.title}`,
      detail: task.source === "agent" ? "Opened by an agent" : null,
      userId: task.completed_by ?? task.created_by ?? null,
    });
  }
  for (const comment of input.comments ?? []) {
    push({
      id: `comment-${comment.id}`,
      at: comment.created_at,
      kind: "comment",
      title: `Comment on ${comment.target_type.replace(/_/g, " ")}`,
      detail: comment.body.slice(0, 160),
      userId: comment.author_user_id ?? null,
    });
  }
  for (const row of input.exports ?? []) {
    push({
      id: `export-${row.id}`,
      at: row.created_at,
      kind: "export",
      title: `Export ${row.format.toUpperCase()}${row.final ? " (final)" : " (draft)"}`,
      detail: row.file_name,
      userId: row.created_by ?? null,
    });
  }

  return events.sort((a, b) => b.at.localeCompare(a.at));
}
