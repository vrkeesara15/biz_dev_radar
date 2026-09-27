/**
 * Typed wrappers over the opportunities routes (M2-15) plus the two contracts
 * that later milestones add: pipeline actions (M6-01) and saved searches
 * (M4-08). Those are not in the generated schema yet, so they go through the
 * same-origin proxy with plain fetch and surface a `NotAvailableError` on 404
 * that the UI turns into an explanatory toast — no server behaviour is stubbed.
 */
import { browserApi, type Schemas } from "@/lib/api/browser";

import type { TzDateInput } from "./dates";
import { toApiQuery, type OpportunityFilters } from "./filters";

export type OpportunityItem = Schemas["OpportunityItem"];
export type OpportunityDetail = Schemas["OpportunityDetail"];
export type OpportunityPage = Schemas["OpportunityPage"];
export type VersionOut = Schemas["VersionOut"];
export type DocumentOut = Schemas["DocumentOut"];
export type Attribution = Schemas["Attribution"];

/** The API's date columns typed loosely enough for both OQ-24 wire shapes. */
export type DateField = TzDateInput;

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** A route that a later milestone provides answered 404. */
export class NotAvailableError extends ApiError {
  constructor(body: unknown) {
    super(404, body, "Not available yet");
    this.name = "NotAvailableError";
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new ApiError(response.status, error);
  return data as T;
}

export const searchOpportunities = (filters: OpportunityFilters, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/opportunities", {
      params: { query: toApiQuery(filters) as never },
      signal,
    }),
  );

export const getOpportunity = (opportunityId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/opportunities/{opportunity_id}", {
      params: { path: { opportunity_id: opportunityId } },
      signal,
    }),
  );

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
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers ?? {}) },
    cache: "no-store",
  });
  const body = await readBody(response);
  if (response.status === 404) throw new NotAvailableError(body);
  if (!response.ok) throw new ApiError(response.status, body);
  return body as T;
}

// --- pipeline actions (M6-01 contract) -----------------------------------------

export type PipelineAction = "pursue" | "watch" | "pass";

export type PipelineActionBody = {
  /** Required for `pass`; free text as SPEC 7 "Pass (with reason)". */
  reason?: string;
  profile_id?: string;
};

export const PIPELINE_UNAVAILABLE_MESSAGE = "Pipeline actions arrive with the pursuits milestone";

export const pipelineAction = (opportunityId: string, action: PipelineAction, body: PipelineActionBody = {}) =>
  jsonRequest<unknown>(`/api/v1/opportunities/${encodeURIComponent(opportunityId)}/${action}`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// --- saved searches (M4-08 contract) ------------------------------------------

export type SavedSearch = {
  id: string;
  name: string;
  /** The search page's query parameters (q, region, type, naics, due_before, status, min_score). */
  filters: Record<string, string>;
  created_at?: string;
};

export const SAVED_SEARCHES_UNAVAILABLE_MESSAGE = "Saved searches arrive with the matching milestone";

export const listSavedSearches = () => jsonRequest<SavedSearch[]>("/api/v1/saved-searches");

export const createSavedSearch = (body: { name: string; filters: Record<string, string> }) =>
  jsonRequest<SavedSearch>("/api/v1/saved-searches", { method: "POST", body: JSON.stringify(body) });

// --- match (M4) -----------------------------------------------------------------

export type MatchSignal = {
  key: string;
  label: string;
  weight: number | null;
  /** 0..1 contribution before weighting, when the API reports it. */
  value: number | null;
  /** Weighted points when the API reports them. */
  points: number | null;
  note: string | null;
};

export type MatchGap = { text: string; fix: string | null };
export type MatchRisk = { text: string; citation: string | null };

export type Match = {
  score: number | null;
  band: "high" | "medium" | "low" | null;
  label: string | null;
  breakdown: MatchSignal[];
  fitSummary: string[];
  matchedCapabilities: string[];
  gaps: MatchGap[];
  risks: MatchRisk[];
  recommendedAction: string | null;
  confidence: number | null;
};

const SIGNAL_LABELS: Record<string, string> = {
  code_match: "Code match",
  codes: "Code match",
  semantic: "Semantic similarity",
  semantic_similarity: "Semantic similarity",
  keywords: "Keyword match",
  keyword_match: "Keyword match",
  eligibility: "Eligibility",
  value_fit: "Value fit",
  geography: "Geography",
  buyer_affinity: "Buyer affinity",
  past_performance: "Past-performance relevance",
};

const num = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

const text = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value.trim() : null);

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((v) => (typeof v === "string" ? v : text((v as { text?: unknown })?.text) ?? "")).filter(Boolean) : [];

export function scoreBand(score: number | null): Match["band"] {
  if (score === null) return null;
  if (score >= 70) return "high";
  if (score >= 50) return "medium";
  return "low";
}

/** Reads whatever shape `match` arrives in (matches.breakdown / rationale jsonb) into a stable view model. */
export function normalizeMatch(raw: unknown): Match | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const score = num(record.score);
  const breakdownRaw = record.breakdown;
  const breakdown: MatchSignal[] = [];
  if (Array.isArray(breakdownRaw)) {
    for (const entry of breakdownRaw) {
      if (!entry || typeof entry !== "object") continue;
      const e = entry as Record<string, unknown>;
      const key = String(e.signal ?? e.key ?? e.name ?? "signal");
      breakdown.push({
        key,
        label: text(e.label) ?? SIGNAL_LABELS[key] ?? key.replace(/_/g, " "),
        weight: num(e.weight),
        value: num(e.value ?? e.raw),
        points: num(e.points ?? e.score ?? e.weighted),
        note: text(e.note ?? e.reason ?? e.detail),
      });
    }
  } else if (breakdownRaw && typeof breakdownRaw === "object") {
    const signals = (breakdownRaw as { signals?: unknown }).signals;
    if (Array.isArray(signals)) return normalizeMatch({ ...record, breakdown: signals });
    for (const [key, value] of Object.entries(breakdownRaw as Record<string, unknown>)) {
      if (value && typeof value === "object") {
        const e = value as Record<string, unknown>;
        breakdown.push({
          key,
          label: text(e.label) ?? SIGNAL_LABELS[key] ?? key.replace(/_/g, " "),
          weight: num(e.weight),
          value: num(e.value ?? e.raw),
          points: num(e.points ?? e.score ?? e.weighted),
          note: text(e.note ?? e.reason ?? e.detail),
        });
      } else {
        breakdown.push({ key, label: SIGNAL_LABELS[key] ?? key.replace(/_/g, " "), weight: null, value: null, points: num(value), note: null });
      }
    }
  }
  const rationale = (record.rationale && typeof record.rationale === "object" ? record.rationale : record) as Record<string, unknown>;
  const gaps: MatchGap[] = Array.isArray(rationale.gaps)
    ? rationale.gaps
        .map((g) => {
          if (typeof g === "string") return { text: g, fix: null };
          if (g && typeof g === "object") {
            const e = g as Record<string, unknown>;
            const body = text(e.text ?? e.gap ?? e.description);
            return body ? { text: body, fix: text(e.suggested_fix ?? e.fix) } : null;
          }
          return null;
        })
        .filter((g): g is MatchGap => !!g)
    : [];
  const risks: MatchRisk[] = Array.isArray(rationale.eligibility_risks ?? rationale.risks)
    ? ((rationale.eligibility_risks ?? rationale.risks) as unknown[])
        .map((r) => {
          if (typeof r === "string") return { text: r, citation: null };
          if (r && typeof r === "object") {
            const e = r as Record<string, unknown>;
            const body = text(e.text ?? e.risk ?? e.description);
            const page = e.page ?? e.page_number;
            const citation = text(e.citation) ?? (page !== undefined && page !== null ? `p. ${page}` : null);
            return body ? { text: body, citation } : null;
          }
          return null;
        })
        .filter((r): r is MatchRisk => !!r)
    : [];
  return {
    score,
    band: scoreBand(score),
    label: text(record.label),
    breakdown,
    fitSummary: strings(rationale.fit_summary),
    matchedCapabilities: strings(rationale.matched_capabilities),
    gaps,
    risks,
    recommendedAction: text(rationale.recommended_action),
    confidence: num(rationale.confidence),
  };
}
