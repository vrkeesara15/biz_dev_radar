/**
 * Display helpers for opportunity records: value ranges in the source currency
 * with the USD normalisation alongside (SPEC 5.3), buyer hierarchy, file sizes.
 */
import { formatMoneyCompact, type Currency } from "@/lib/money";

export type ValueRange = {
  /** "₹1.20 Cr – ₹2.00 Cr", "$250.0K", "from $1.00M", or null when nothing is stated. */
  primary: string | null;
  /** USD equivalent when the source currency is not USD and the API normalised it. */
  usd: string | null;
};

function asCurrency(code: string | null | undefined): Currency | null {
  return code === "USD" || code === "INR" ? code : null;
}

function range(
  min: string | number | null | undefined,
  max: string | number | null | undefined,
  currency: Currency,
): string | null {
  const hasMin = min !== null && min !== undefined && min !== "";
  const hasMax = max !== null && max !== undefined && max !== "";
  if (!hasMin && !hasMax) return null;
  const lo = hasMin ? formatMoneyCompact(min, currency) : null;
  const hi = hasMax ? formatMoneyCompact(max, currency) : null;
  if (lo && hi) return lo === hi ? lo : `${lo} – ${hi}`;
  if (lo) return `from ${lo}`;
  return `up to ${hi}`;
}

export function formatValueRange(record: {
  currency: string;
  estimated_value_min: string | null;
  estimated_value_max: string | null;
  estimated_value_min_usd: string | null;
  estimated_value_max_usd: string | null;
}): ValueRange {
  const currency = asCurrency(record.currency);
  const primary = currency ? range(record.estimated_value_min, record.estimated_value_max, currency) : null;
  const usdText = range(record.estimated_value_min_usd, record.estimated_value_max_usd, "USD");
  if (!primary) return { primary: usdText, usd: null };
  return { primary, usd: currency !== "USD" && usdText ? usdText : null };
}

/** "Agency › Sub-tier › Office" from the hierarchy array or the three buyer columns. */
export function buyerPath(record: {
  buyer_org: string | null;
  buyer_sub_org: string | null;
  buyer_office: string | null;
  buyer_hierarchy?: string[];
}): string[] {
  const fromArray = (record.buyer_hierarchy ?? []).map((s) => s.trim()).filter(Boolean);
  if (fromArray.length) return fromArray;
  return [record.buyer_org, record.buyer_sub_org, record.buyer_office]
    .map((s) => (s ?? "").trim())
    .filter(Boolean);
}

export function formatBytes(size: number | null | undefined): string | null {
  if (size === null || size === undefined || !Number.isFinite(size) || size < 0) return null;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** Field-level version diff entry to "old → new" strings. */
export function formatDiffValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    if (value.every((v) => typeof v === "string" || typeof v === "number")) return value.join(", ");
    if (value.every((v) => v && typeof v === "object" && ("file_name" in v || "url" in v))) {
      return value
        .map((v) => {
          const doc = v as { file_name?: string | null; url?: string };
          return doc.file_name || doc.url || "document";
        })
        .join(", ");
    }
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Human labels for core.changes.ChangeKind. */
export const CHANGE_KIND_LABELS: Record<string, string> = {
  deadline_moved: "Deadline moved",
  new_attachment: "New attachment",
  qa_posted: "Q&A posted",
  cancelled: "Cancelled",
  awarded: "Awarded",
  description_updated: "Description updated",
  status_changed: "Status changed",
  other: "Other change",
};

export function changeKindLabel(kind: string): string {
  return CHANGE_KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}
