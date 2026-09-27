/**
 * Client contract for POST /api/v1/profiles/{profile_id}/autofill (task M1-10,
 * built concurrently). The endpoint only *suggests*; accepting a suggestion
 * writes through the normal profile endpoints in `applySuggestion`.
 *
 * Suggestion `field` grammar (dotted path, `[]` marks a list append):
 *   "legal_name"           -> profile column (PUT)
 *   "addresses[]"          -> append to a profile array column (PUT)
 *   "codes.naics[]"        -> POST /codes with scheme=naics
 *   "service_lines[]"      -> POST /service-lines
 *   "certifications[]"     -> POST /certifications
 *   "past_performance[]"   -> POST /past-performance
 */
import type { Profile } from "@/lib/api/browser";
import {
  createItem,
  updateProfile,
  type Resource,
} from "@/lib/onboarding/api";
import {
  CERTIFICATION_KINDS,
  fieldLabel,
  fieldMeta,
  isAllowedInRegion,
  isFieldAllowed,
  optionsForRegion,
  CODE_SCHEMES,
  type Region,
  type StepId,
} from "@/lib/profile-fields";

export type AutofillSource = "website" | "capability_pdf" | "uei";

export interface AutofillRequest {
  website_url?: string;
  capability_file_id?: string;
  uei?: string;
}

export interface AutofillSuggestion {
  field: string;
  value: unknown;
  source: AutofillSource;
  source_ref: string;
  confidence: number;
}

export interface AutofillResponse {
  suggestions: AutofillSuggestion[];
  warnings: string[];
}

export type AutofillResult =
  | { status: "ok"; data: AutofillResponse }
  | { status: "unavailable" }
  | { status: "error"; message: string };

export const HIGH_CONFIDENCE = 0.8;

export const SOURCE_LABELS: Record<AutofillSource, string> = {
  website: "Website",
  capability_pdf: "Capability statement",
  uei: "SAM.gov (UEI)",
};

/** Calls the autofill endpoint; a 404/405/501 means it is not deployed yet. */
export async function requestAutofill(
  profileId: string,
  request: AutofillRequest,
  fetcher: typeof fetch = (...args) => globalThis.fetch(...args),
): Promise<AutofillResult> {
  let response: Response;
  try {
    response = await fetcher(`/api/v1/profiles/${encodeURIComponent(profileId)}/autofill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch (error) {
    return { status: "error", message: error instanceof Error ? error.message : "Network error" };
  }
  if (response.status === 404 || response.status === 405 || response.status === 501) {
    return { status: "unavailable" };
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `Autofill failed (${response.status})`;
    return { status: "error", message };
  }
  const data = body as Partial<AutofillResponse> | null;
  return {
    status: "ok",
    data: {
      suggestions: Array.isArray(data?.suggestions) ? data.suggestions : [],
      warnings: Array.isArray(data?.warnings) ? data.warnings : [],
    },
  };
}

// --- field grammar ---------------------------------------------------------

export type ParsedField = { root: string; sub: string | null; isList: boolean };

export function parseSuggestionField(field: string): ParsedField {
  const isList = field.endsWith("[]");
  const bare = isList ? field.slice(0, -2) : field;
  const [root, ...rest] = bare.split(".");
  return { root, sub: rest.length ? rest.join(".") : null, isList };
}

const LIST_RESOURCES: Record<string, Resource> = {
  codes: "codes",
  keywords: "keywords",
  service_lines: "service-lines",
  certifications: "certifications",
  teaming_partners: "teaming-partners",
  past_performance: "past-performance",
  personnel: "personnel",
  registrations: "registrations",
  vehicles: "vehicles",
  insurance: "insurance",
  rate_card: "rate-card",
  // OQ-54: the autofill route emits the company overview as boilerplate[]
  boilerplate: "boilerplate",
};

const SOCIO_ECONOMIC = new Set(
  CERTIFICATION_KINDS.filter((k) => k.family === "socio_economic").map((k) => k.value),
);

/** Field-metadata key a suggestion maps to (drives step + region checks). */
export function suggestionFieldKey(s: AutofillSuggestion): string {
  const { root, sub } = parseSuggestionField(s.field);
  if (root === "codes" && sub) return `codes.${sub}`;
  if (root === "certifications") {
    const kind = (s.value as { kind?: string } | null)?.kind;
    return kind && SOCIO_ECONOMIC.has(kind) ? "certifications.socio_economic" : "certifications.security";
  }
  if (root === "net_worth_amount" || root === "net_worth_currency") return "net_worth";
  if (root === "bonding_capacity_amount" || root === "bonding_capacity_currency") return "bonding_capacity";
  if (root.startsWith("value_") && root.endsWith("_usd")) return "value_range_usd";
  if (root.startsWith("value_") && root.endsWith("_inr")) return "value_range_inr";
  return root;
}

export function suggestionStep(s: AutofillSuggestion): StepId {
  return fieldMeta(suggestionFieldKey(s))?.step ?? 1;
}

export function suggestionLabel(s: AutofillSuggestion): string {
  return fieldLabel(suggestionFieldKey(s));
}

/** A suggestion for the other region's field is never shown or applied. */
export function isSuggestionAllowed(s: AutofillSuggestion, region: Region): boolean {
  const key = suggestionFieldKey(s);
  if (!isFieldAllowed(key, region)) return false;
  const { root, sub } = parseSuggestionField(s.field);
  if (root === "codes" && sub) {
    return optionsForRegion(CODE_SCHEMES, region).some((o) => o.value === sub);
  }
  if (root === "certifications") {
    const kind = (s.value as { kind?: string } | null)?.kind;
    const meta = CERTIFICATION_KINDS.find((k) => k.value === kind);
    return meta ? isAllowedInRegion(meta.region, region) : true;
  }
  return true;
}

export type IndexedSuggestion = AutofillSuggestion & { index: number };

export function groupSuggestionsByStep(
  suggestions: readonly AutofillSuggestion[],
): Map<StepId, IndexedSuggestion[]> {
  const groups = new Map<StepId, IndexedSuggestion[]>();
  suggestions.forEach((s, index) => {
    const step = suggestionStep(s);
    const list = groups.get(step) ?? [];
    list.push({ ...s, index });
    groups.set(step, list);
  });
  return new Map([...groups.entries()].sort((a, b) => a[0] - b[0]));
}

export function highConfidenceIndexes(
  suggestions: readonly AutofillSuggestion[],
  threshold = HIGH_CONFIDENCE,
): number[] {
  return suggestions.flatMap((s, i) => (s.confidence >= threshold ? [i] : []));
}

export function formatSuggestionValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(formatSuggestionValue).join(", ");
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const preferred = ["title", "name", "legal_name", "code", "term", "kind", "line1", "city"];
    const parts = preferred.filter((k) => record[k] !== undefined).map((k) => formatSuggestionValue(record[k]));
    if (parts.length) return parts.join(" · ");
    return Object.entries(record)
      .map(([k, v]) => `${k}: ${formatSuggestionValue(v)}`)
      .join(", ");
  }
  return String(value);
}

// --- apply -----------------------------------------------------------------

export type ApplyContext = { profileId: string; region: Region; profile: Profile };

export type ApplyOutcome = { profile: Profile | null; resource: Resource | null };

/**
 * Writes an accepted suggestion through the normal endpoints. Scalars and
 * array appends PUT the profile; list resources POST an item. Returns the
 * updated profile when the profile itself changed.
 */
export async function applySuggestion(ctx: ApplyContext, s: AutofillSuggestion): Promise<ApplyOutcome> {
  if (!isSuggestionAllowed(s, ctx.region)) {
    throw new Error(`"${s.field}" is not available for region ${ctx.region}`);
  }
  const { root, sub, isList } = parseSuggestionField(s.field);

  const resource = LIST_RESOURCES[root];
  if (resource) {
    let body: Record<string, unknown>;
    if (root === "codes") {
      const value = s.value as string | { code?: string; is_primary?: boolean; scheme?: string };
      body =
        typeof value === "string"
          ? { scheme: sub, code: value, is_primary: false }
          : { scheme: sub ?? value.scheme, code: value.code, is_primary: Boolean(value.is_primary) };
    } else if (root === "keywords" && typeof s.value === "string") {
      body = { kind: "include", term: s.value, weight: 1 };
    } else {
      body = (s.value ?? {}) as Record<string, unknown>;
    }
    await createItem(ctx.profileId, resource, body as never);
    return { profile: null, resource };
  }

  if (isList) {
    const current = (ctx.profile as unknown as Record<string, unknown>)[root];
    const existing = Array.isArray(current) ? current : [];
    const additions = Array.isArray(s.value) ? s.value : [s.value];
    const merged = [...existing];
    for (const item of additions) {
      if (!merged.some((m) => JSON.stringify(m) === JSON.stringify(item))) merged.push(item);
    }
    const profile = await updateProfile(ctx.profileId, { [root]: merged });
    return { profile, resource: null };
  }

  const profile = await updateProfile(ctx.profileId, { [root]: s.value });
  return { profile, resource: null };
}
