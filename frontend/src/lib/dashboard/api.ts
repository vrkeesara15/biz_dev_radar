/**
 * Home dashboard (SPEC 10.4 screen 2).
 *
 * GET /api/v1/dashboard is the M6 aggregate:
 *   { open_by_stage, due_next_7_days[], pipeline_value_by_stage { USD, INR },
 *     win_rate, avg_hours_saved_per_package, alert_precision }
 * It is not on main yet, so the call is a plain fetch through the same-origin
 * proxy and a 404 turns into `null` — Home then renders the empty state for
 * those cards instead of pretending to have numbers. The reader below is
 * deliberately tolerant about the shape of `pipeline_value_by_stage` (per
 * stage, or one total per currency) so the card survives either reading.
 */
import { NotAvailableError, ApiError } from "@/lib/opportunities/api";
import type { Currency } from "@/lib/money";

export type DueSoonItem = {
  id: string;
  title: string;
  /** ISO 8601; the key date that falls inside the window. */
  due_at: string | null;
  kind: string | null;
  stage: string | null;
  buyer: string | null;
  opportunity_id: string | null;
  pursuit_id: string | null;
};

export type StageValue = { USD: number | null; INR: number | null };

export type Dashboard = {
  openByStage: { stage: string; count: number }[];
  dueNext7Days: DueSoonItem[];
  pipelineValueByStage: { stage: string; value: StageValue }[];
  pipelineTotal: StageValue;
  winRate: number | null;
  avgHoursSavedPerPackage: number | null;
  alertPrecision: number | null;
};

/** SPEC 9 pipeline stages, in board order; unknown stages are appended as-is. */
export const STAGE_ORDER = [
  "identified",
  "reviewing",
  "bid",
  "no_bid",
  "drafting",
  "in_review",
  "submitted",
  "won",
  "lost",
] as const;

export function stageLabel(stage: string): string {
  const spaced = stage.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

const str = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

const byStageOrder = (a: { stage: string }, b: { stage: string }) => {
  const order = STAGE_ORDER as readonly string[];
  const ai = order.indexOf(a.stage);
  const bi = order.indexOf(b.stage);
  if (ai === -1 && bi === -1) return a.stage.localeCompare(b.stage);
  if (ai === -1) return 1;
  if (bi === -1) return -1;
  return ai - bi;
};

const CURRENCIES: Currency[] = ["USD", "INR"];

function readStageValue(raw: unknown): StageValue {
  if (raw === null || raw === undefined) return { USD: null, INR: null };
  if (typeof raw === "number" || typeof raw === "string") return { USD: num(raw), INR: null };
  const record = raw as Record<string, unknown>;
  return {
    USD: num(record.USD ?? record.usd),
    INR: num(record.INR ?? record.inr),
  };
}

function sumStageValues(values: StageValue[]): StageValue {
  const total: StageValue = { USD: null, INR: null };
  for (const currency of CURRENCIES) {
    const amounts = values.map((value) => value[currency]).filter((v): v is number => v !== null);
    if (amounts.length) total[currency] = amounts.reduce((a, b) => a + b, 0);
  }
  return total;
}

/** Reads the aggregate into the view model Home renders. */
export function normalizeDashboard(raw: unknown): Dashboard {
  const record = (raw ?? {}) as Record<string, unknown>;

  const openRaw = (record.open_by_stage ?? {}) as Record<string, unknown>;
  const openByStage = Object.entries(openRaw)
    .map(([stage, count]) => ({ stage, count: num(count) ?? 0 }))
    .sort(byStageOrder);

  const dueRaw = Array.isArray(record.due_next_7_days) ? record.due_next_7_days : [];
  const dueNext7Days: DueSoonItem[] = dueRaw
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item, index) => ({
      id: str(item.id) ?? str(item.pursuit_id) ?? str(item.opportunity_id) ?? `due-${index}`,
      title: str(item.title) ?? str(item.name) ?? "Untitled",
      due_at: str(item.due_at ?? item.date ?? item.response_due_at ?? item.due_on),
      kind: str(item.kind ?? item.date_kind ?? item.type),
      stage: str(item.stage),
      buyer: str(item.buyer ?? item.buyer_org),
      opportunity_id: str(item.opportunity_id),
      pursuit_id: str(item.pursuit_id),
    }));

  const pipelineRaw = record.pipeline_value_by_stage;
  let pipelineValueByStage: { stage: string; value: StageValue }[] = [];
  let pipelineTotal: StageValue = { USD: null, INR: null };
  if (pipelineRaw && typeof pipelineRaw === "object") {
    const entries = Object.entries(pipelineRaw as Record<string, unknown>);
    const currencyKeyed = entries.every(([key]) => key === "USD" || key === "INR" || key === "usd" || key === "inr");
    if (currencyKeyed) {
      // one total per currency, no stage breakdown
      pipelineTotal = readStageValue(pipelineRaw);
    } else {
      pipelineValueByStage = entries
        .map(([stage, value]) => ({ stage, value: readStageValue(value) }))
        .sort(byStageOrder);
      pipelineTotal = sumStageValues(pipelineValueByStage.map((entry) => entry.value));
    }
  }

  return {
    openByStage,
    dueNext7Days,
    pipelineValueByStage,
    pipelineTotal,
    winRate: num(record.win_rate),
    avgHoursSavedPerPackage: num(record.avg_hours_saved_per_package),
    alertPrecision: num(record.alert_precision),
  };
}

export const DASHBOARD_UNAVAILABLE_MESSAGE = "Pipeline metrics arrive with the pursuits milestone";

/** `null` when the route is not deployed yet; throws on any other failure. */
export async function getDashboard(signal?: AbortSignal): Promise<Dashboard | null> {
  const response = await fetch("/api/v1/dashboard", {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (response.status === 404) throw new NotAvailableError(null);
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
