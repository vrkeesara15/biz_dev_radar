/**
 * Completeness meter logic (SPEC 4: matching from 40, drafting needs 70 plus
 * at least 3 past performances). The API's `completeness` object is the
 * authority; these helpers turn it into badges and readable missing items.
 */
import { STEPS, type StepId } from "@/lib/profile-fields";

export const MATCHING_THRESHOLD = 40;
export const DRAFTING_THRESHOLD = 70;
export const MIN_PAST_PERFORMANCE = 3;

export type CompletenessLike = {
  score: number;
  matching_enabled: boolean;
  drafting_enabled: boolean;
  missing: string[];
  sections?: Record<string, { score: number; weight: number; missing: string[] }>;
};

export type BadgeTone = "on" | "off";
export type CompletenessBadge = {
  id: "matching" | "drafting";
  label: string;
  tone: BadgeTone;
  /** Short explanation shown as a tooltip/description. */
  detail: string;
};

export const EMPTY_COMPLETENESS: CompletenessLike = {
  score: 0,
  matching_enabled: false,
  drafting_enabled: false,
  missing: [],
  sections: {},
};

/**
 * Badges for the two capability gates. The API booleans decide the tone; the
 * detail text explains what is left, using the past-performance count when
 * the caller knows it.
 */
export function completenessBadges(
  completeness: CompletenessLike,
  pastPerformanceCount?: number,
): CompletenessBadge[] {
  const matching: CompletenessBadge = completeness.matching_enabled
    ? { id: "matching", label: "Matching on", tone: "on", detail: `Score ${completeness.score} is at least ${MATCHING_THRESHOLD}.` }
    : {
        id: "matching",
        label: "Matching off",
        tone: "off",
        detail: `Reach ${MATCHING_THRESHOLD} (currently ${completeness.score}) to start matching.`,
      };

  let draftingDetail: string;
  if (completeness.drafting_enabled) {
    draftingDetail = `Score ${completeness.score} with ${MIN_PAST_PERFORMANCE}+ past performances.`;
  } else {
    const reasons: string[] = [];
    if (completeness.score < DRAFTING_THRESHOLD) {
      reasons.push(`reach ${DRAFTING_THRESHOLD} (currently ${completeness.score})`);
    }
    if (pastPerformanceCount !== undefined && pastPerformanceCount < MIN_PAST_PERFORMANCE) {
      reasons.push(`add ${MIN_PAST_PERFORMANCE - pastPerformanceCount} more past performance`);
    } else if (pastPerformanceCount === undefined && completeness.score >= DRAFTING_THRESHOLD) {
      reasons.push(`add at least ${MIN_PAST_PERFORMANCE} past performances`);
    }
    draftingDetail = reasons.length ? `To draft: ${reasons.join(" and ")}.` : "Drafting is not enabled yet.";
  }
  const drafting: CompletenessBadge = {
    id: "drafting",
    label: completeness.drafting_enabled ? "Drafting on" : "Drafting off",
    tone: completeness.drafting_enabled ? "on" : "off",
    detail: draftingDetail,
  };
  return [matching, drafting];
}

const SECTION_STEP: Record<string, StepId> = {
  identity: 1,
  registrations: 1,
  size_finance: 2,
  what_we_sell: 3,
  where_how_big: 4,
  proof: 5,
  preferences: 6,
};

const ITEM_LABELS: Record<string, string> = {
  legal_name: "Legal name",
  address: "An address",
  website: "Website",
  phone: "Phone",
  bid_inbox_email: "Bid-inbox email",
  year_founded: "Year founded",
  legal_structure: "Legal structure",
  uei: "UEI",
  sam_status: "SAM status",
  sam_expires_on: "SAM expiry date",
  cage_code: "CAGE code",
  pan: "PAN",
  gstin: "GSTIN",
  cin_llpin: "CIN / LLPIN",
  udyam_or_dpiit: "Udyam or DPIIT number",
  gem_seller_id: "GeM seller ID",
  employee_count_total: "Employee count",
  annual_revenue: "Annual revenue",
  bonding_capacity: "Bonding capacity",
  audited_fiscal_years: "Audited fiscal years",
  net_worth: "Net worth",
  solvency_certificate: "Solvency certificate",
  codes: "Codes",
  primary_naics: "Primary NAICS",
  primary_india_category: "Primary GeM / India category",
  keywords: "Keywords",
  service_lines: "Service lines",
  capability_statement: "Capability statement",
  target_geography: "Target geography",
  value_range: "Contract value range",
  notice_types_wanted: "Notice types",
  buyers: "Target or blocked buyers",
  past_performance: "Past performance",
  personnel: "Key personnel",
  certifications_or_insurance: "Certifications or insurance",
  boilerplate: "Boilerplate",
  rate_card: "Rate card",
  scoring_weights_reviewed: "Fit-score weights reviewed",
  required_approver_roles: "Required approvers",
  output_languages: "Output languages",
  notification_prefs: "Notification preferences",
};

export type MissingItem = { key: string; label: string; step: StepId; section: string };

/** Turns "section.item" keys into readable items with the step they live in. */
export function describeMissing(missing: readonly string[]): MissingItem[] {
  return missing.map((key) => {
    const [section, ...rest] = key.split(".");
    const item = rest.join(".") || section;
    const step = SECTION_STEP[section] ?? 7;
    const label = ITEM_LABELS[item] ?? item.replace(/_/g, " ");
    return { key, label, step, section };
  });
}

/** The first `limit` missing items in API order (the API lists sections by weight). */
export function topMissing(missing: readonly string[], limit = 3): MissingItem[] {
  return describeMissing(missing).slice(0, limit);
}

export function stepTitle(step: StepId): string {
  return STEPS.find((s) => s.id === step)?.title ?? `Step ${step}`;
}

/** Progress of the stepper as a 0-100 percentage (step 7 of 7 = 100). */
export function stepProgress(current: StepId): number {
  return Math.round(((current - 1) / (STEPS.length - 1)) * 100);
}
