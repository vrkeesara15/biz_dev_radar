/**
 * Pipeline filters and their URL form (SPEC 9: "filters by owner, due date,
 * value, region, stage").
 *
 * The board and the table share one filter state, and that state lives in the
 * query string so a filtered board is a link: `parsePipelineFilters` reads it,
 * `serializePipelineFilters` writes it back with a stable key order and no
 * default values, and `toPursuitQuery` turns it into the query parameters of
 * GET /api/v1/pursuits (M6-01).
 *
 * `view` rides along in the same query string so switching board ↔ table keeps
 * the filters and is back-button friendly. Pagination belongs to the table
 * only — the board loads every page of the current filter up to BOARD_CAP.
 */
import { STAGES, isStage, type Stage } from "./stages";

export type PipelineView = "board" | "table";
export const PIPELINE_VIEWS = ["board", "table"] as const;

export const REGIONS = ["us", "in"] as const;
export type PipelineRegion = (typeof REGIONS)[number];
export const REGION_LABELS: Record<PipelineRegion, string> = {
  us: "United States",
  in: "India",
};

export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;
/** GET /api/v1/pursuits caps page_size at 200 (M6-01). */
export const API_MAX_PAGE_SIZE = 200;
/** How many cards the board will draw before it stops and says so. */
export const BOARD_CAP = 500;

export type PipelineFilters = {
  view: PipelineView;
  /** owner_user_id, or "unassigned" for the cards nobody owns. */
  owner: string | null;
  /** ISO date (YYYY-MM-DD) in the user's calendar; null when unset. */
  due_before: string | null;
  /** Minimum estimated value in USD; null when unset. */
  min_value: number | null;
  region: PipelineRegion | null;
  stage: Stage[];
  /** true = watched only, false = not watched, null = both. */
  watch: boolean | null;
  page: number;
  page_size: number;
};

export const UNASSIGNED = "unassigned";

export const DEFAULT_PIPELINE_FILTERS: PipelineFilters = {
  view: "board",
  owner: null,
  due_before: null,
  min_value: null,
  region: null,
  stage: [],
  watch: null,
  page: 1,
  page_size: DEFAULT_PAGE_SIZE,
};

/** Query-string keys, in the order they are written. */
export const PIPELINE_FILTER_KEYS = [
  "view",
  "owner",
  "due_before",
  "min_value",
  "region",
  "stage",
  "watch",
  "page",
  "page_size",
] as const;
export type PipelineFilterKey = (typeof PIPELINE_FILTER_KEYS)[number];

/** Keys that describe the search itself (view and pagination are not filters). */
const FILTER_ONLY_KEYS: PipelineFilterKey[] = [
  "owner",
  "due_before",
  "min_value",
  "region",
  "stage",
  "watch",
];

const isRegion = (value: string): value is PipelineRegion =>
  (REGIONS as readonly string[]).includes(value);
const isView = (value: string): value is PipelineView =>
  (PIPELINE_VIEWS as readonly string[]).includes(value);

/** Splits a comma/space separated list, trims and drops empties and duplicates. */
export function splitList(value: string | null | undefined): string[] {
  if (!value) return [];
  const out: string[] = [];
  for (const part of value.split(/[\s,;]+/)) {
    const clean = part.trim();
    if (clean && !out.includes(clean)) out.push(clean);
  }
  return out;
}

function parseIsoDate(value: string | null): string | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value.trim());
  if (!match) return null;
  const [, y, m, d] = match;
  const probe = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)));
  if (Number.isNaN(probe.getTime()) || probe.getUTCMonth() !== Number(m) - 1) return null;
  return `${y}-${m}-${d}`;
}

function parseAmount(value: string | null): number | null {
  if (value === null || value === "") return null;
  const n = Number(value.replace(/[,\s]/g, ""));
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}

function parseBool(value: string | null): boolean | null {
  if (value === null || value === "") return null;
  const text = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(text)) return true;
  if (["0", "false", "no", "off"].includes(text)) return false;
  return null;
}

function parsePositiveInt(value: string | null, fallback: number, max = Number.MAX_SAFE_INTEGER): number {
  if (value === null || value === "") return fallback;
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1) return fallback;
  return Math.min(n, max);
}

type ParamSource =
  | URLSearchParams
  | string
  | Record<string, string | string[] | undefined | null>
  | null
  | undefined;

function toSearchParams(source: ParamSource): URLSearchParams {
  if (!source) return new URLSearchParams();
  if (source instanceof URLSearchParams) return source;
  if (typeof source === "string") return new URLSearchParams(source.replace(/^\?/, ""));
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(source)) {
    if (value === undefined || value === null) continue;
    params.set(key, Array.isArray(value) ? value.join(",") : String(value));
  }
  return params;
}

/**
 * Reads the pipeline filters from a query string, URLSearchParams or a plain
 * record. Unknown values are dropped rather than failing, so a stale link
 * still renders the closest valid board.
 */
export function parsePipelineFilters(source: ParamSource): PipelineFilters {
  const params = toSearchParams(source);
  const view = (params.get("view") ?? "").toLowerCase();
  const region = (params.get("region") ?? "").toLowerCase();
  const owner = (params.get("owner") ?? "").trim();
  return {
    view: isView(view) ? view : "board",
    owner: owner ? owner : null,
    due_before: parseIsoDate(params.get("due_before")),
    min_value: parseAmount(params.get("min_value")),
    region: isRegion(region) ? region : null,
    stage: splitList(params.get("stage"))
      .map((s) => s.toLowerCase())
      .filter(isStage),
    watch: parseBool(params.get("watch")),
    page: parsePositiveInt(params.get("page"), 1),
    page_size: parsePositiveInt(params.get("page_size"), DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE),
  };
}

/** The filter record: the same keys as the URL, defaults omitted. */
export function pipelineFiltersToRecord(
  filters: PipelineFilters,
  options: { view?: boolean; page?: boolean } = {},
): Record<string, string> {
  const out: Record<string, string> = {};
  if (options.view && filters.view !== "board") out.view = filters.view;
  if (filters.owner) out.owner = filters.owner;
  if (filters.due_before) out.due_before = filters.due_before;
  if (filters.min_value !== null) out.min_value = String(filters.min_value);
  if (filters.region) out.region = filters.region;
  if (filters.stage.length) out.stage = filters.stage.join(",");
  if (filters.watch !== null) out.watch = filters.watch ? "true" : "false";
  if (options.page) {
    if (filters.page > 1) out.page = String(filters.page);
    if (filters.page_size !== DEFAULT_PAGE_SIZE) out.page_size = String(filters.page_size);
  }
  return out;
}

/** Query string (no leading "?") with stable key order and defaults omitted. */
export function serializePipelineFilters(filters: PipelineFilters): string {
  const record = pipelineFiltersToRecord(filters, { view: true, page: filters.view === "table" });
  const params = new URLSearchParams();
  for (const key of PIPELINE_FILTER_KEYS) {
    if (record[key] !== undefined) params.set(key, record[key]);
  }
  return params.toString();
}

/** Path + query for the pipeline page. */
export function pipelineHref(filters: Partial<PipelineFilters>): string {
  const query = serializePipelineFilters({ ...DEFAULT_PIPELINE_FILTERS, ...filters });
  return query ? `/app/pipeline?${query}` : "/app/pipeline";
}

/** Number of filter controls in use (view and pagination excluded). */
export function activePipelineFilterCount(filters: PipelineFilters): number {
  const record = pipelineFiltersToRecord(filters);
  return FILTER_ONLY_KEYS.filter((key) => record[key] !== undefined).length;
}

/** True when the two states describe the same search (view and page ignored). */
export function samePipelineSearch(a: PipelineFilters, b: PipelineFilters): boolean {
  const key = (f: PipelineFilters) => JSON.stringify(pipelineFiltersToRecord(f));
  return key(a) === key(b);
}

export type PursuitQuery = Record<string, string | number | boolean>;

/**
 * Query parameters for GET /api/v1/pursuits. `due_before` is sent as the end
 * of that calendar day in UTC so a deadline on the chosen date is included,
 * and `owner=unassigned` is dropped (the API has no such filter) and applied
 * client-side instead.
 */
export function toPursuitQuery(
  filters: PipelineFilters,
  options: { page?: number; pageSize?: number } = {},
): PursuitQuery {
  const query: PursuitQuery = {
    page: options.page ?? filters.page,
    page_size: options.pageSize ?? filters.page_size,
  };
  if (filters.owner && filters.owner !== UNASSIGNED) query.owner = filters.owner;
  if (filters.due_before) query.due_before = `${filters.due_before}T23:59:59Z`;
  if (filters.min_value !== null) query.min_value = filters.min_value;
  if (filters.region) query.region = filters.region;
  if (filters.stage.length) query.stage = filters.stage.join(",");
  if (filters.watch !== null) query.watch = filters.watch;
  return query;
}

/** `owner=unassigned` has no server-side equivalent, so the client applies it. */
export function needsClientOwnerFilter(filters: PipelineFilters): boolean {
  return filters.owner === UNASSIGNED;
}

/** Short human summary, for the "showing…" line. */
export function describePipelineFilters(filters: PipelineFilters, ownerName?: string | null): string {
  const parts: string[] = [];
  if (filters.owner) parts.push(filters.owner === UNASSIGNED ? "Unassigned" : `Owner ${ownerName ?? filters.owner}`);
  if (filters.due_before) parts.push(`due by ${filters.due_before}`);
  if (filters.min_value !== null) parts.push(`value ≥ $${filters.min_value.toLocaleString("en-US")}`);
  if (filters.region) parts.push(REGION_LABELS[filters.region]);
  if (filters.stage.length) parts.push(`${filters.stage.length} of ${STAGES.length} stages`);
  if (filters.watch !== null) parts.push(filters.watch ? "watched" : "not watched");
  return parts.length ? parts.join(" · ") : "All pursuits";
}
