/**
 * Home dashboard (SPEC 10.4 screen 2) over GET /api/v1/dashboard (M6-08).
 *
 * The response is SPEC 9's KPI list:
 *   { generated_at, open_by_stage, due_next_7_days[], pipeline_value_by_stage,
 *     pipeline_value_total, win_rate, awarded, lost, submitted,
 *     avg_hours_saved_per_package, hours_saved_total, hours_saved_basis,
 *     alert_precision, alert_feedback_rated }
 *
 * Three of those are deliberately nullable and mean "we do not know yet"
 * rather than zero (OQ-134, OQ-135): `win_rate` until a pursuit has been won
 * or lost, `alert_precision` until somebody rates an alert. Home says so in
 * words instead of printing 0%. Hours saved is an ESTIMATE and travels with
 * `hours_saved_basis` saying what it is based on — the label is rendered, so
 * the number is never mistaken for a measurement.
 *
 * Money arrives as decimal strings in both currencies, already reconciled by
 * the server from one USD figure (OQ-133), so the two columns can never
 * disagree and the client never converts.
 */
import type { Schemas } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import { STAGES, stageLabel, stageOrder } from "@/lib/pursuits/stages";

export type DashboardOut = Schemas["DashboardOut"];
export type DueSoon = Schemas["DueSoonOut"];
export type MoneyPair = Schemas["MoneyPairOut"];

export type StageCount = { stage: string; label: string; count: number };
export type StageValue = { stage: string; label: string; usd: number | null; inr: number | null };

export type Dashboard = {
  generatedAt: string;
  openByStage: StageCount[];
  openTotal: number;
  dueNext7Days: DueSoon[];
  pipelineValueByStage: StageValue[];
  pipelineTotal: { usd: number | null; inr: number | null };
  /** 0..1, or null while no pursuit has been awarded or lost. */
  winRate: number | null;
  awarded: number;
  lost: number;
  submitted: number;
  avgHoursSavedPerPackage: number;
  hoursSavedTotal: number;
  /** Plain-English basis for the hours-saved estimate (OQ-134). */
  hoursSavedBasis: string;
  /** 0..1, or null while nobody has rated an alert. */
  alertPrecision: number | null;
  alertFeedbackRated: number;
};

export { stageLabel };

/** SPEC 9 board order, re-exported so Home and the board never diverge. */
export const STAGE_ORDER = STAGES;

const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

const int = (value: unknown): number => num(value) ?? 0;

const byStage = (a: { stage: string }, b: { stage: string }) => {
  const delta = stageOrder(a.stage) - stageOrder(b.stage);
  return delta !== 0 ? delta : a.stage.localeCompare(b.stage);
};

function readMoneyPair(raw: unknown): { usd: number | null; inr: number | null } {
  if (!raw || typeof raw !== "object") return { usd: null, inr: null };
  const record = raw as Record<string, unknown>;
  return { usd: num(record.USD ?? record.usd), inr: num(record.INR ?? record.inr) };
}

/** Reads the aggregate into the view model Home renders. */
export function normalizeDashboard(raw: unknown): Dashboard {
  const record = (raw ?? {}) as Record<string, unknown>;

  const openRaw = (record.open_by_stage ?? {}) as Record<string, unknown>;
  const openByStage: StageCount[] = Object.entries(openRaw)
    .map(([stage, count]) => ({ stage, label: stageLabel(stage), count: int(count) }))
    .sort(byStage);

  const valueRaw = (record.pipeline_value_by_stage ?? {}) as Record<string, unknown>;
  const pipelineValueByStage: StageValue[] = Object.entries(valueRaw)
    .map(([stage, value]) => ({ stage, label: stageLabel(stage), ...readMoneyPair(value) }))
    .sort(byStage);

  const dueRaw = Array.isArray(record.due_next_7_days) ? record.due_next_7_days : [];

  return {
    generatedAt: typeof record.generated_at === "string" ? record.generated_at : "",
    openByStage,
    openTotal: openByStage.reduce((total, row) => total + row.count, 0),
    dueNext7Days: dueRaw as DueSoon[],
    pipelineValueByStage,
    pipelineTotal: readMoneyPair(record.pipeline_value_total),
    winRate: num(record.win_rate),
    awarded: int(record.awarded),
    lost: int(record.lost),
    submitted: int(record.submitted),
    avgHoursSavedPerPackage: int(record.avg_hours_saved_per_package),
    hoursSavedTotal: int(record.hours_saved_total),
    hoursSavedBasis:
      typeof record.hours_saved_basis === "string" && record.hours_saved_basis.trim()
        ? record.hours_saved_basis.trim()
        : "an estimate per submitted package",
    alertPrecision: num(record.alert_precision),
    alertFeedbackRated: int(record.alert_feedback_rated),
  };
}

/** SPEC 9: a rate with an empty denominator is unknown, never zero (OQ-134). */
export const WIN_RATE_EMPTY = "Not enough decisions yet";
export const ALERT_PRECISION_EMPTY = "Collecting feedback";

export function formatRate(rate: number | null, empty: string): string {
  return rate === null ? empty : `${Math.round(rate * 100)}%`;
}

export async function getDashboard(signal?: AbortSignal): Promise<Dashboard> {
  const response = await fetch("/api/v1/dashboard", {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, body);
  return normalizeDashboard(body);
}
