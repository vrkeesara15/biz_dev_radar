/**
 * Pure formatting for the admin console (SPEC 10.4 screen 9).
 *
 * The API meters LLM spend in integer micro-dollars (backend OQ-46) and reports
 * usage periods as "YYYY-MM", so every conversion lives here and is unit-tested
 * rather than being re-derived in each component.
 */

export const MICRO_USD = 1_000_000;

export type HealthTone = "ok" | "warn" | "bad" | "muted";

/** Micro-dollars as a currency string; small amounts keep more decimals. */
export function formatUsd(microUsd: number | null | undefined): string {
  const micro = Number(microUsd ?? 0);
  if (!Number.isFinite(micro)) return "$0.00";
  const usd = micro / MICRO_USD;
  if (usd === 0) return "$0.00";
  const abs = Math.abs(usd);
  const digits = abs >= 0.01 ? 2 : abs >= 0.0001 ? 4 : 6;
  return usd.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Compact token counts: 940, 1.5K, 12.3M. */
export function formatTokens(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n)) return "0";
  if (Math.abs(n) < 1000) return String(Math.trunc(n));
  if (Math.abs(n) < 1_000_000) return `${trim(n / 1000)}K`;
  if (Math.abs(n) < 1_000_000_000) return `${trim(n / 1_000_000)}M`;
  return `${trim(n / 1_000_000_000)}B`;
}

function trim(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

const MONTH_PATTERN = /^(\d{4})-(0[1-9]|1[0-2])$/;

export function isPeriod(value: string): boolean {
  return MONTH_PATTERN.test(value);
}

/** The "YYYY-MM" period containing `date` (UTC, like the backend). */
export function currentPeriod(date: Date = new Date()): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Shift a period by whole months; throws on a malformed period. */
export function shiftPeriod(period: string, months: number): string {
  const match = MONTH_PATTERN.exec(period);
  if (!match) throw new Error(`not a YYYY-MM period: ${period}`);
  const total = Number(match[1]) * 12 + (Number(match[2]) - 1) + months;
  const year = Math.floor(total / 12);
  const month = total - year * 12 + 1;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`;
}

/** The last `count` periods, newest first, ending at `from`. */
export function recentPeriods(count = 12, from: string = currentPeriod()): string[] {
  return Array.from({ length: Math.max(1, count) }, (_, i) => shiftPeriod(from, -i));
}

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** "2026-04" -> "April 2026"; anything else is returned unchanged. */
export function formatPeriod(period: string): string {
  const match = MONTH_PATTERN.exec(period);
  if (!match) return period;
  return `${MONTH_NAMES[Number(match[2]) - 1]} ${match[1]}`;
}

/** Colour family for a source/system health status. */
export function healthTone(status: string | null | undefined): HealthTone {
  switch (status) {
    case "ok":
      return "ok";
    case "degraded":
    case "unconfigured":
      return "warn";
    case "failing":
      return "bad";
    default:
      return "muted";
  }
}

/** Human label for a health or run status. */
export function healthLabel(status: string | null | undefined): string {
  if (!status) return "Unknown";
  return status
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Short absolute timestamp, e.g. "14 Apr 2026, 09:12". Empty for null. */
export function formatTimestamp(value: string | null | undefined, timeZone?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }).format(date);
}

/** "3m ago", "2h ago", "5d ago"; "just now" under a minute, "—" when missing. */
export function relativeTime(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return "—";
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return "—";
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 0) return "in the future";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** Duration between two ISO instants as "1m 05s"; "—" while a run is open. */
export function formatDuration(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  if (!startedAt || !finishedAt) return "—";
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}

/** Bar length (0-100) of each value relative to the largest one. */
export function barPercents(values: readonly number[]): number[] {
  const max = values.reduce((acc, v) => (Number.isFinite(v) && v > acc ? v : acc), 0);
  if (max <= 0) return values.map(() => 0);
  return values.map((v) => (Number.isFinite(v) && v > 0 ? Math.max(1, Math.round((v / max) * 100)) : 0));
}
