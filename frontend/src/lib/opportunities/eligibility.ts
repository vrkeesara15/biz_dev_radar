/**
 * Turns the `eligibility` jsonb of an opportunity (SPEC 5.3: turnover,
 * experience, certifications, registrations, exemptions, LLM-extracted with
 * page citations) into a flat list of checks the detail page can render with
 * pass / fail / unknown icons.
 *
 * Three shapes are known today:
 *   - an evaluation (core.eligibility_in.EvaluationOut.as_dict): {status, score,
 *     blocking[], exemptions[], results[{name, status, reason, ...}]}
 *   - extracted criteria (core.eligibility_in.CriteriaIn): {min_avg_turnover_inr,
 *     required_certifications, requires_dsc, ...}
 *   - Grants.gov applicant data (core.normalize.grants.map_eligibility)
 * Anything else is listed field by field as "unknown" so nothing is hidden.
 */

export type CheckStatus = "pass" | "fail" | "unknown";

export type EligibilityCheck = {
  key: string;
  label: string;
  status: CheckStatus;
  detail: string | null;
  /** "p. 12" when the extractor cited a page. */
  citation: string | null;
};

const STATUS_VALUES: CheckStatus[] = ["pass", "fail", "unknown"];

const LABELS: Record<string, string> = {
  min_avg_turnover_inr: "Minimum average turnover",
  turnover_years: "Turnover years averaged",
  min_experience_years: "Minimum experience",
  required_certifications: "Required certifications",
  emd_amount_inr: "EMD amount",
  allows_mse_exemption: "MSE exemption",
  allows_startup_exemption: "Startup exemption",
  requires_gem_registration: "GeM registration",
  gem_registration: "GeM registration",
  gem_seller_id: "GeM seller ID",
  dsc: "Digital signature certificate",
  mse: "MSE exemption",
  startup: "Startup exemption",
  requires_dsc: "Digital signature certificate",
  due_on: "Bid due date",
  applicant_types: "Eligible applicant types",
  applicant_type_codes: "Applicant type codes",
  funding_instruments: "Funding instruments",
  funding_categories: "Funding categories",
  eligibility_text: "Eligibility as published",
  cost_sharing: "Cost sharing required",
  estimated_funding: "Estimated funding",
  number_of_awards: "Number of awards",
  set_aside: "Set-aside",
  size_status: "Size status",
  registrations: "Registrations",
  certifications: "Certifications",
  experience: "Experience",
  turnover: "Turnover",
  exemptions: "Exemptions",
};

export function humanizeKey(key: string): string {
  if (LABELS[key]) return LABELS[key];
  const text = key.replace(/[_-]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : key;
}

function asStatus(value: unknown): CheckStatus | null {
  if (typeof value === "boolean") return value ? "pass" : "fail";
  if (typeof value !== "string") return null;
  const lower = value.toLowerCase();
  if ((STATUS_VALUES as string[]).includes(lower)) return lower as CheckStatus;
  if (lower === "met" || lower === "ok" || lower === "eligible" || lower === "true") return "pass";
  if (lower === "not_met" || lower === "failed" || lower === "ineligible" || lower === "false") return "fail";
  return null;
}

function formatScalar(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const parts = value.map(formatScalar).filter((p): p is string => !!p);
    return parts.length ? parts.join(", ") : null;
  }
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => {
        const text = formatScalar(v);
        return text ? `${humanizeKey(k)}: ${text}` : null;
      })
      .filter((p): p is string => !!p);
    return parts.length ? parts.join("; ") : null;
  }
  return String(value);
}

function citationOf(record: Record<string, unknown>): string | null {
  const page = record.page ?? record.page_number ?? record.source_page;
  if (typeof page === "number" || (typeof page === "string" && page)) return `p. ${page}`;
  const citation = record.citation ?? record.source;
  if (typeof citation === "string" && citation) return citation;
  if (Array.isArray(record.pages) && record.pages.length) return `pp. ${record.pages.join(", ")}`;
  return null;
}

function fromResult(record: Record<string, unknown>, index: number): EligibilityCheck {
  const key = String(record.name ?? record.key ?? record.criterion ?? record.id ?? `check-${index + 1}`);
  const status = asStatus(record.status) ?? asStatus(record.passed) ?? asStatus(record.met) ?? "unknown";
  const detailParts: string[] = [];
  for (const field of ["reason", "detail", "requirement", "required", "actual", "measured", "note"]) {
    const text = formatScalar(record[field]);
    if (text) detailParts.push(field === "required" ? `Required: ${text}` : field === "actual" || field === "measured" ? `Actual: ${text}` : text);
  }
  if (record.exemption_applied) detailParts.push(`Exemption applied: ${formatScalar(record.exemption_applied)}`);
  return {
    key,
    label: humanizeKey(key),
    status,
    detail: detailParts.length ? detailParts.join(" · ") : null,
    citation: citationOf(record),
  };
}

export type EligibilitySummary = {
  overall: CheckStatus | null;
  score: number | null;
  blocking: string[];
  exemptions: string[];
  checks: EligibilityCheck[];
};

const META_KEYS = new Set(["status", "score", "blocking", "exemptions", "results", "checks", "criteria", "extracted_at", "model", "version", "source"]);

/** Normalises any eligibility jsonb into checks; an empty object yields no checks. */
export function eligibilityChecks(raw: unknown): EligibilitySummary {
  const empty: EligibilitySummary = { overall: null, score: null, blocking: [], exemptions: [], checks: [] };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    if (Array.isArray(raw)) {
      return { ...empty, checks: raw.filter((r) => r && typeof r === "object").map((r, i) => fromResult(r as Record<string, unknown>, i)) };
    }
    return empty;
  }
  const record = raw as Record<string, unknown>;
  const overall = asStatus(record.status);
  const scoreNum = record.score === undefined || record.score === null ? NaN : Number(record.score);
  const score = Number.isFinite(scoreNum) ? scoreNum : null;
  const blocking = Array.isArray(record.blocking) ? record.blocking.map(String) : [];
  const exemptions = Array.isArray(record.exemptions) ? record.exemptions.map(String) : [];

  const evaluated = record.results ?? record.checks ?? record.criteria;
  const checks: EligibilityCheck[] = [];
  if (Array.isArray(evaluated)) {
    evaluated.forEach((entry, index) => {
      if (entry && typeof entry === "object") checks.push(fromResult(entry as Record<string, unknown>, index));
    });
  }
  for (const [key, value] of Object.entries(record)) {
    if (META_KEYS.has(key)) continue;
    if (value && typeof value === "object" && !Array.isArray(value) && "status" in (value as object)) {
      checks.push({ ...fromResult(value as Record<string, unknown>, checks.length), key, label: humanizeKey(key) });
      continue;
    }
    const detail = formatScalar(value);
    if (detail === null) continue;
    checks.push({ key, label: humanizeKey(key), status: "unknown", detail, citation: null });
  }
  for (const check of checks) {
    if (blocking.includes(check.key) && check.status === "unknown") check.status = "fail";
  }
  return { overall, score, blocking, exemptions, checks };
}
