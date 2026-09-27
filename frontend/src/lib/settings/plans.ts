/**
 * Plans and limits (SPEC 3), mirroring `backend/app/core/plan.py`.
 *
 * Pure: the comparison table the billing page renders, the rank that decides
 * whether a plan is an upgrade, and the arithmetic behind each usage bar.
 * `limit === null` means unlimited, exactly as the API sends it.
 */
export const PLANS = ["free", "pro", "enterprise"] as const;
export type Plan = (typeof PLANS)[number];

export const PLAN_LABELS: Record<Plan, string> = {
  free: "Free",
  pro: "Pro",
  enterprise: "Enterprise",
};

/** Resource keys as `LimitUsage.resource` reports them. */
export const RESOURCE_LABELS: Record<string, string> = {
  profiles: "Company profiles",
  source_regions: "Source regions",
  instant_alerts: "Instant alerts",
  agent_drafts_per_month: "Agent drafts per month",
  agent_budget_usd_month: "Agent LLM budget (USD per month)",
};

export function resourceLabel(resource: string): string {
  return RESOURCE_LABELS[resource] ?? resource.replace(/_/g, " ");
}

export type PlanFeature = {
  /** Row label in the comparison table. */
  label: string;
  values: Record<Plan, string>;
};

/** SPEC 3 "Plans:" sentence, one row per thing that differs. */
export const PLAN_COMPARISON: readonly PlanFeature[] = [
  { label: "Company profiles", values: { free: "1", pro: "3", enterprise: "Unlimited" } },
  { label: "Source regions", values: { free: "1", pro: "All", enterprise: "All" } },
  { label: "Alerts", values: { free: "Digest only", pro: "Instant + digest", enterprise: "Instant + digest" } },
  { label: "Agent drafts per month", values: { free: "—", pro: "10", enterprise: "Unlimited" } },
  { label: "Agent LLM budget", values: { free: "—", pro: "USD 50 / month", enterprise: "Unlimited" } },
  { label: "SSO", values: { free: "—", pro: "—", enterprise: "Yes" } },
  { label: "Private LLM key", values: { free: "—", pro: "—", enterprise: "Yes" } },
  { label: "Data residency", values: { free: "US or India", pro: "US or India", enterprise: "US or India, contractual" } },
] as const;

const RANK: Record<Plan, number> = { free: 0, pro: 1, enterprise: 2 };

export const planRank = (plan: string): number => RANK[plan as Plan] ?? -1;

/** True when moving from `current` to `target` is a paid upgrade. */
export function isUpgrade(current: string, target: Plan): boolean {
  return planRank(target) > planRank(current);
}

/** Checkout exists for the paid plans only (the API refuses `free`). */
export const PAID_PLANS: readonly Plan[] = ["pro", "enterprise"];
export const isPaidPlan = (plan: string): plan is Plan =>
  (PAID_PLANS as readonly string[]).includes(plan);

/** The call to action for a plan column, given the plan in force. */
export function planAction(current: string, target: Plan): "current" | "upgrade" | "downgrade" {
  if (current === target) return "current";
  return isUpgrade(current, target) ? "upgrade" : "downgrade";
}

export type LimitUsage = { resource: string; limit: number | null; used: number; remaining: number | null };

export type UsageBar = {
  resource: string;
  label: string;
  used: number;
  limit: number | null;
  remaining: number | null;
  /** 0..100 for the progress bar; an unlimited resource sits at 0. */
  percent: number;
  unlimited: boolean;
  atLimit: boolean;
  /** "3 of 10", "2 used · unlimited", "0 of 0 — not on this plan". */
  text: string;
};

/** Turns one `LimitUsage` row into everything the bar needs. */
export function usageBar(row: LimitUsage): UsageBar {
  const unlimited = row.limit === null;
  const limit = row.limit ?? 0;
  const used = Math.max(0, row.used);
  const percent = unlimited || limit === 0 ? 0 : Math.min(100, Math.round((used / limit) * 100));
  const atLimit = !unlimited && used >= limit;
  const label = resourceLabel(row.resource);
  const text = unlimited
    ? `${used.toLocaleString()} used · unlimited`
    : limit === 0
      ? "Not included on this plan"
      : `${used.toLocaleString()} of ${limit.toLocaleString()}`;
  return { resource: row.resource, label, used, limit: row.limit, remaining: row.remaining, percent, unlimited, atLimit, text };
}

/** The bars, in the SPEC 3 order, with anything unexpected appended. */
export function usageBars(rows: LimitUsage[]): UsageBar[] {
  const order = Object.keys(RESOURCE_LABELS);
  return [...rows]
    .sort((a, b) => {
      const ai = order.indexOf(a.resource);
      const bi = order.indexOf(b.resource);
      if (ai === -1 && bi === -1) return a.resource.localeCompare(b.resource);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    })
    .map(usageBar);
}

/** Resources already at their ceiling, for the "you need Pro" note. */
export const exhausted = (rows: LimitUsage[]): UsageBar[] => usageBars(rows).filter((bar) => bar.atLimit);

export const PROVIDER_LABELS: Record<string, string> = { stripe: "Stripe", razorpay: "Razorpay" };

/** SPEC 3 / M7-04: the provider follows the tenant's region, and GST is India's. */
export const needsGst = (provider: string): boolean => provider === "razorpay";

/** GST state codes are two digits; the API validates the GSTIN itself. */
export function gstErrors(fields: { gstin?: string; place_of_supply?: string }): Record<string, string> {
  const errors: Record<string, string> = {};
  const gstin = (fields.gstin ?? "").trim();
  const pos = (fields.place_of_supply ?? "").trim();
  if (gstin && !/^[0-9A-Z]{15}$/i.test(gstin)) errors.gstin = "A GSTIN is 15 characters.";
  if (pos && !/^\d{2}$/.test(pos)) errors.place_of_supply = "Two-digit GST state code, e.g. 29.";
  if (gstin && !pos) errors.place_of_supply = "Place of supply is needed for a GST invoice.";
  return errors;
}
