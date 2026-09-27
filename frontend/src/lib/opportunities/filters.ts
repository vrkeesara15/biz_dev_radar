/**
 * Opportunity search filters and their URL form.
 *
 * The filter state lives in the page's query string so a saved search is just
 * a URL: `parseFilters` reads it, `serializeFilters` writes it back with a
 * stable key order and no default values, and `toApiQuery` turns the same
 * state into the query parameters of GET /api/v1/opportunities (SPEC 10.3).
 * Saved searches (M4-08) store the very same query-string record.
 */

/** SPEC 5.3 notice_type enum, in display order. */
export const NOTICE_TYPES = [
  "rfp",
  "rfq",
  "rfi",
  "sources_sought",
  "presolicitation",
  "combined",
  "grant",
  "forecast",
  "award",
  "eoi",
  "gem_bid",
  "reverse_auction",
  "corrigendum",
  "special",
] as const;
export type NoticeType = (typeof NOTICE_TYPES)[number];

export const NOTICE_TYPE_LABELS: Record<NoticeType, string> = {
  rfp: "RFP",
  rfq: "RFQ",
  rfi: "RFI",
  sources_sought: "Sources sought",
  presolicitation: "Presolicitation",
  combined: "Combined synopsis",
  grant: "Grant",
  forecast: "Forecast",
  award: "Award",
  eoi: "EOI",
  gem_bid: "GeM bid",
  reverse_auction: "Reverse auction",
  corrigendum: "Corrigendum",
  special: "Special",
};

/** SPEC 5.3 status enum. */
export const STATUSES = ["open", "closing_soon", "closed", "cancelled", "awarded"] as const;
export type OpportunityStatus = (typeof STATUSES)[number];

export const STATUS_LABELS: Record<OpportunityStatus, string> = {
  open: "Open",
  closing_soon: "Closing soon",
  closed: "Closed",
  cancelled: "Cancelled",
  awarded: "Awarded",
};

export const REGIONS = ["us", "in"] as const;
export type FilterRegion = (typeof REGIONS)[number];
export const REGION_LABELS: Record<FilterRegion, string> = { us: "United States", in: "India" };

export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;

export type OpportunityFilters = {
  q: string;
  region: FilterRegion | null;
  type: NoticeType[];
  naics: string[];
  /** ISO date (YYYY-MM-DD) in the user's calendar; null when unset. */
  due_before: string | null;
  status: OpportunityStatus[];
  /** 0..100 or null when unset. */
  min_score: number | null;
  page: number;
  page_size: number;
};

export const DEFAULT_FILTERS: OpportunityFilters = {
  q: "",
  region: null,
  type: [],
  naics: [],
  due_before: null,
  status: [],
  min_score: null,
  page: 1,
  page_size: DEFAULT_PAGE_SIZE,
};

/** Query-string keys, in the order they are written. */
export const FILTER_KEYS = [
  "q",
  "region",
  "type",
  "naics",
  "due_before",
  "status",
  "min_score",
  "page",
  "page_size",
] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

const isNoticeType = (value: string): value is NoticeType =>
  (NOTICE_TYPES as readonly string[]).includes(value);
const isStatus = (value: string): value is OpportunityStatus =>
  (STATUSES as readonly string[]).includes(value);
const isRegion = (value: string): value is FilterRegion =>
  (REGIONS as readonly string[]).includes(value);

/** Splits a comma/space separated list, trims and drops empties (order kept, no duplicates). */
export function splitList(value: string | null | undefined): string[] {
  if (!value) return [];
  const out: string[] = [];
  for (const part of value.split(/[\s,;]+/)) {
    const clean = part.trim();
    if (clean && !out.includes(clean)) out.push(clean);
  }
  return out;
}

/** NAICS codes are 2–6 digits; anything else typed in the box is ignored. */
export function parseNaicsList(value: string | null | undefined): string[] {
  return splitList(value).filter((code) => /^\d{2,6}$/.test(code));
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

function parseScore(value: string | null): number | null {
  if (value === null || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.min(100, Math.max(0, Math.round(n)));
}

function parsePositiveInt(value: string | null, fallback: number, max = Number.MAX_SAFE_INTEGER): number {
  if (value === null || value === "") return fallback;
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1) return fallback;
  return Math.min(n, max);
}

type ParamSource = URLSearchParams | string | Record<string, string | string[] | undefined | null> | null | undefined;

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
 * Reads filters from a query string, URLSearchParams or a plain record (a
 * saved search's `filters`). Unknown values are dropped rather than failing,
 * so a stale link still renders the closest valid search.
 */
export function parseFilters(source: ParamSource): OpportunityFilters {
  const params = toSearchParams(source);
  const region = params.get("region")?.toLowerCase() ?? "";
  return {
    q: (params.get("q") ?? "").trim(),
    region: isRegion(region) ? region : null,
    type: splitList(params.get("type")).map((t) => t.toLowerCase()).filter(isNoticeType),
    naics: parseNaicsList(params.get("naics")),
    due_before: parseIsoDate(params.get("due_before")),
    status: splitList(params.get("status")).map((s) => s.toLowerCase()).filter(isStatus),
    min_score: parseScore(params.get("min_score")),
    page: parsePositiveInt(params.get("page"), 1),
    page_size: parsePositiveInt(params.get("page_size"), DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE),
  };
}

/** The filter record a saved search stores: the same keys as the URL, page omitted. */
export function filtersToRecord(filters: OpportunityFilters, options: { page?: boolean } = {}): Record<string, string> {
  const out: Record<string, string> = {};
  if (filters.q) out.q = filters.q;
  if (filters.region) out.region = filters.region;
  if (filters.type.length) out.type = filters.type.join(",");
  if (filters.naics.length) out.naics = filters.naics.join(",");
  if (filters.due_before) out.due_before = filters.due_before;
  if (filters.status.length) out.status = filters.status.join(",");
  if (filters.min_score !== null) out.min_score = String(filters.min_score);
  if (options.page) {
    if (filters.page > 1) out.page = String(filters.page);
    if (filters.page_size !== DEFAULT_PAGE_SIZE) out.page_size = String(filters.page_size);
  }
  return out;
}

/** Query string (no leading "?") with stable key order and defaults omitted. */
export function serializeFilters(filters: OpportunityFilters): string {
  const record = filtersToRecord(filters, { page: true });
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    if (record[key] !== undefined) params.set(key, record[key]);
  }
  return params.toString();
}

/** Path + query for the search page. */
export function searchHref(filters: Partial<OpportunityFilters>): string {
  const query = serializeFilters({ ...DEFAULT_FILTERS, ...filters });
  return query ? `/app/opportunities?${query}` : "/app/opportunities";
}

/** True when the two filter sets describe the same search (page ignored). */
export function sameSearch(a: OpportunityFilters, b: OpportunityFilters): boolean {
  return serializeFilters({ ...a, page: 1, page_size: DEFAULT_PAGE_SIZE }) ===
    serializeFilters({ ...b, page: 1, page_size: DEFAULT_PAGE_SIZE });
}

/** Number of non-default filter controls in use (for the "Clear" affordance). */
export function activeFilterCount(filters: OpportunityFilters): number {
  return Object.keys(filtersToRecord(filters)).length;
}

/**
 * Query parameters for GET /api/v1/opportunities. `due_before` is sent as the
 * end of that calendar day in UTC so a deadline on the chosen date is included.
 */
export function toApiQuery(filters: OpportunityFilters): Record<string, string | number> {
  const query: Record<string, string | number> = {
    page: filters.page,
    page_size: filters.page_size,
  };
  if (filters.q) query.q = filters.q;
  if (filters.region) query.region = filters.region;
  if (filters.type.length) query.type = filters.type.join(",");
  if (filters.naics.length) query.naics = filters.naics.join(",");
  if (filters.due_before) query.due_before = `${filters.due_before}T23:59:59Z`;
  if (filters.status.length) query.status = filters.status.join(",");
  if (filters.min_score !== null) query.min_score = filters.min_score;
  return query;
}

/** Short human summary of a filter record, for saved-search chips. */
export function describeFilters(filters: OpportunityFilters): string {
  const parts: string[] = [];
  if (filters.q) parts.push(`“${filters.q}”`);
  if (filters.region) parts.push(REGION_LABELS[filters.region]);
  if (filters.type.length) parts.push(filters.type.map((t) => NOTICE_TYPE_LABELS[t]).join("/"));
  if (filters.naics.length) parts.push(`NAICS ${filters.naics.join(", ")}`);
  if (filters.status.length) parts.push(filters.status.map((s) => STATUS_LABELS[s]).join("/"));
  if (filters.due_before) parts.push(`due by ${filters.due_before}`);
  if (filters.min_score !== null) parts.push(`score ≥ ${filters.min_score}`);
  return parts.length ? parts.join(" · ") : "All opportunities";
}
