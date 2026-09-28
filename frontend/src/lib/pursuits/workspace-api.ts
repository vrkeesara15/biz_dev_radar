/**
 * The pursuit-workspace routes (M5-16, M5-13, M5-14, M5-10): drafts,
 * compliance matrix, submission packet, agent runs, the two gates and the
 * exports. Everything but the scorecard probe is in the generated OpenAPI
 * schema and goes through `browserApi` and the same-origin proxy.
 */
import { browserApi, type Schemas } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";

export type Matrix = Schemas["PursuitMatrixOut"];
export type MatrixRow = Schemas["MatrixRowOut"];
export type FormatRules = Schemas["FormatRules"];
export type ChecklistItem = Schemas["ChecklistItem"];
export type Packet = Schemas["PursuitPacketOut"];
export type DraftList = Schemas["DraftListOut"];
export type DraftSummary = Schemas["DraftSummaryOut"];
export type Draft = Schemas["DraftOut"];
export type DraftVersion = Schemas["DraftVersionOut"];
export type DraftFeedbackList = Schemas["DraftFeedbackListOut"];
export type Export = Schemas["ExportOut"];
export type ExportList = Schemas["ExportListOut"];
export type DecisionResult = Schemas["DecisionOut"];
export type RunAgentsResult = Schemas["RunAgentsOut"];
export type ApproveBudgetResult = Schemas["ApproveBudgetOut"];
export type ApprovePackageResult = Schemas["ApprovePackageOut"];
export type MarkFinalResult = Schemas["MarkFinalOut"];
export type Run = Schemas["RunOut"];

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new ApiError(response.status, error);
  return data as T;
}

const path = (pursuitId: string) => ({ pursuit_id: pursuitId });

// --- compliance matrix, checklist and packet -----------------------------------

export const getMatrix = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/matrix", {
      params: { path: path(pursuitId) },
      signal,
    }),
  );

export const getPacket = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/packet", {
      params: { path: path(pursuitId) },
      signal,
    }),
  );

// --- drafts ---------------------------------------------------------------------

export const listDrafts = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/drafts", {
      params: { path: path(pursuitId) },
      signal,
    }),
  );

export const getDraft = (pursuitId: string, sectionId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/drafts/{section_id}", {
      params: { path: { ...path(pursuitId), section_id: sectionId } },
      signal,
    }),
  );

export type DraftPut = {
  body_html: string;
  base_version: number;
  title?: string | null;
  volume?: string | null;
};

/** Optimistic save: a stale `base_version` answers 409 (see `asStaleVersion`). */
export const putDraft = (pursuitId: string, sectionId: string, body: DraftPut) =>
  unwrap(
    browserApi.PUT("/api/v1/pursuits/{pursuit_id}/drafts/{section_id}", {
      params: { path: { ...path(pursuitId), section_id: sectionId } },
      body: body as never,
    }),
  );

export const approveDraft = (pursuitId: string, sectionId: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/drafts/{section_id}/approve", {
      params: { path: { ...path(pursuitId), section_id: sectionId } },
    }),
  );

export const listDraftFeedback = (pursuitId: string, sectionId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/drafts/{section_id}/feedback", {
      params: { path: { ...path(pursuitId), section_id: sectionId } },
      signal,
    }),
  );

// --- agent runs and the cost guard ----------------------------------------------

export const runAgents = (pursuitId: string, step: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/agents/run", {
      params: { path: path(pursuitId) },
      body: { step } as never,
    }),
  );

export const approveBudget = (pursuitId: string, additionalUsd: string, reason?: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/agents/approve-budget", {
      params: { path: path(pursuitId) },
      body: { additional_usd: additionalUsd, reason: reason || null } as never,
    }),
  );

// --- the two gates ---------------------------------------------------------------

export const recordDecision = (pursuitId: string, decision: "bid" | "no_bid", note?: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/decision", {
      params: { path: path(pursuitId) },
      body: { decision, note: note || null } as never,
    }),
  );

export const approvePackage = (pursuitId: string, note?: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/approve-package", {
      params: { path: path(pursuitId) },
      body: { note: note || null } as never,
    }),
  );

export const markFinal = (pursuitId: string, note?: string) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/mark-final", {
      params: { path: path(pursuitId) },
      body: { note: note || null } as never,
    }),
  );

// --- exports ----------------------------------------------------------------------

export const EXPORT_FORMATS = ["docx", "pdf", "xlsx", "zip"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export const EXPORT_LABELS: Record<ExportFormat, string> = {
  docx: "DOCX",
  pdf: "PDF",
  xlsx: "XLSX",
  zip: "ZIP",
};

/** 202 {id, format, file_name, url, expires_at, final, renderer}. */
export const createExport = (pursuitId: string, format: ExportFormat) =>
  unwrap(
    browserApi.POST("/api/v1/pursuits/{pursuit_id}/export", {
      params: { path: path(pursuitId), query: { format } },
    }),
  );

export const listExports = (pursuitId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/pursuits/{pursuit_id}/exports", {
      params: { path: path(pursuitId) },
      signal,
    }),
  );

// --- the profile behind the pursuit (Gate 1 approver roles) -----------------------

export const getProfile = (profileId: string, signal?: AbortSignal) =>
  unwrap(
    browserApi.GET("/api/v1/profiles/{profile_id}", {
      params: { path: { profile_id: profileId } },
      signal,
    }),
  );

// --- the bid/no-bid scorecard artifact (not in the schema yet, OQ-147) -------------

/**
 * `pursuit_artifacts` rows of kind `scorecard` are written by agent 4 but no
 * route serves them, so this is an untyped probe of the route the backend is
 * expected to grow (`GET /pursuits/{id}/artifacts?kind=scorecard`). Anything
 * other than a 200 with a readable body is treated as "no scorecard yet" and
 * the tab says so: the workspace never fails because of it.
 */
export async function probeArtifact(
  pursuitId: string,
  kind: string,
  signal?: AbortSignal,
): Promise<unknown | null> {
  try {
    const response = await fetch(
      `/api/v1/pursuits/${encodeURIComponent(pursuitId)}/artifacts?kind=${encodeURIComponent(kind)}`,
      { headers: { Accept: "application/json" }, cache: "no-store", signal },
    );
    if (!response.ok) return null;
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}
