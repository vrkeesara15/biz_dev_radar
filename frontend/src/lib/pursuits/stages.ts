/**
 * Pipeline stages on the client (SPEC 9), mirroring backend
 * `app.core.pursuit_stages`.
 *
 * SPEC 9's order is the board's column order:
 *   Identified → Qualifying → Bid decision → Drafting → In review →
 *   Final approval → Submitted → Awarded / Lost / Cancelled / No-bid
 *
 * The four outcomes are terminal: they are grouped under one collapsible
 * "Closed" section rather than four always-open columns, so a board with a
 * year of history still fits on a laptop. The server owns the transition
 * rules — the board only shows what happened and surfaces the 409 `reason`
 * when a drop is refused (OQ-110, M6-01).
 */

/** The linear ladder, in SPEC 9 order. */
export const LADDER_STAGES = [
  "identified",
  "qualifying",
  "bid_decision",
  "drafting",
  "in_review",
  "final_approval",
  "submitted",
] as const;

/** The four outcomes; grouped under "Closed" on the board. */
export const TERMINAL_STAGES = ["awarded", "lost", "cancelled", "no_bid"] as const;

export const STAGES = [...LADDER_STAGES, ...TERMINAL_STAGES] as const;

export type LadderStage = (typeof LADDER_STAGES)[number];
export type TerminalStage = (typeof TERMINAL_STAGES)[number];
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  identified: "Identified",
  qualifying: "Qualifying",
  bid_decision: "Bid decision",
  drafting: "Drafting",
  in_review: "In review",
  final_approval: "Final approval",
  submitted: "Submitted",
  awarded: "Awarded",
  lost: "Lost",
  cancelled: "Cancelled",
  no_bid: "No-bid",
};

/** The heading of the collapsible section holding the four terminal stages. */
export const CLOSED_SECTION_LABEL = "Closed";

export function isStage(value: unknown): value is Stage {
  return typeof value === "string" && (STAGES as readonly string[]).includes(value);
}

export function isTerminalStage(value: string): boolean {
  return (TERMINAL_STAGES as readonly string[]).includes(value);
}

/** "bid_decision" -> "Bid decision"; an unknown stage is title-cased, never dropped. */
export function stageLabel(stage: string): string {
  if (isStage(stage)) return STAGE_LABELS[stage];
  const spaced = stage.replace(/_/g, " ").trim();
  if (!spaced) return "Unknown";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Position on the ladder; -1 for a terminal or unknown stage. */
export function ladderIndex(stage: string): number {
  return (LADDER_STAGES as readonly string[]).indexOf(stage);
}

/** Board order for any stage string: ladder first, then outcomes, unknown last. */
export function stageOrder(stage: string): number {
  const ladder = ladderIndex(stage);
  if (ladder !== -1) return ladder;
  const terminal = (TERMINAL_STAGES as readonly string[]).indexOf(stage);
  if (terminal !== -1) return LADDER_STAGES.length + terminal;
  return STAGES.length;
}

/** Sorts any list of stage strings into SPEC 9 order (unknown stages last, alphabetical). */
export function sortStages(stages: readonly string[]): string[] {
  return [...stages].sort((a, b) => {
    const delta = stageOrder(a) - stageOrder(b);
    return delta !== 0 ? delta : a.localeCompare(b);
  });
}

export type BoardColumn = {
  stage: string;
  label: string;
  count: number;
  terminal: boolean;
};

export type BoardColumns = {
  /** The seven ladder columns, always shown, always in SPEC 9 order. */
  open: BoardColumn[];
  /** Awarded / Lost / Cancelled / No-bid, plus any unknown stage the API sent. */
  closed: BoardColumn[];
  /** Total of the four terminal columns (the "Closed" section badge). */
  closedCount: number;
};

/**
 * The board's columns from the API's `by_stage` counts. Every ladder stage
 * gets a column even at zero (an empty column is a drop target), and a stage
 * the server knows about but this build does not is appended to "Closed"
 * rather than silently swallowing its cards.
 */
export function boardColumns(byStage: Record<string, number> | null | undefined): BoardColumns {
  const counts = byStage ?? {};
  const count = (stage: string) => {
    const raw = counts[stage];
    const n = typeof raw === "number" ? raw : Number(raw);
    return Number.isFinite(n) ? n : 0;
  };
  const open: BoardColumn[] = LADDER_STAGES.map((stage) => ({
    stage,
    label: STAGE_LABELS[stage],
    count: count(stage),
    terminal: false,
  }));
  const extra = sortStages(Object.keys(counts).filter((stage) => !isStage(stage)));
  const closed: BoardColumn[] = [
    ...TERMINAL_STAGES.map((stage) => ({
      stage: stage as string,
      label: STAGE_LABELS[stage],
      count: count(stage),
      terminal: true,
    })),
    ...extra.map((stage) => ({ stage, label: stageLabel(stage), count: count(stage), terminal: true })),
  ];
  return { open, closed, closedCount: closed.reduce((total, column) => total + column.count, 0) };
}

export type GateBadge = {
  key: "bid_decision" | "package_approval" | "matrix_recheck";
  label: string;
  tone: "warning" | "danger";
};

/**
 * The gates SPEC 9 makes visible on a card, derived from the fields the API
 * exposes today (OQ-136): Gate 1 is `decision` on a card sitting in Bid
 * decision, Gate 2 is a card sitting in Final approval, and an amendment after
 * drafting started sets `matrix_recheck_required` (M6-06).
 */
export function gateBadges(item: {
  stage: string;
  decision?: string | null;
  matrix_recheck_required?: boolean | null;
}): GateBadge[] {
  const badges: GateBadge[] = [];
  if (item.stage === "bid_decision" && !item.decision) {
    badges.push({ key: "bid_decision", label: "Needs bid decision", tone: "warning" });
  }
  if (item.stage === "final_approval") {
    badges.push({ key: "package_approval", label: "Needs package approval", tone: "warning" });
  }
  if (item.matrix_recheck_required) {
    badges.push({ key: "matrix_recheck", label: "Re-check compliance", tone: "danger" });
  }
  return badges;
}

/** "E2E Owner" -> "EO"; an email falls back to its first two letters. */
export function initials(name: string | null | undefined, email?: string | null): string {
  const source = (name ?? "").trim() || (email ?? "").split("@")[0]?.replace(/[._-]+/g, " ").trim() || "";
  if (!source) return "?";
  const words = source.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}
