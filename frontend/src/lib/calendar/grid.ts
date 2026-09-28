/**
 * Calendar grid arithmetic for the month and week views (SPEC 10.4 screen 7).
 *
 * No calendar library: a month is six-or-fewer rows of seven civil dates, and
 * a week is one row. The grid itself is pure civil-date arithmetic done on
 * UTC midnights (so a DST change can never shift a cell), and the only place
 * a time zone matters is `dayKey`, which asks "on which of the viewer's days
 * does this instant fall?" — that is what makes an event at 02:30 IST show up
 * on Oct 15 for a Delhi reader and on Oct 14 for a New York one.
 */
import { zoneParts } from "@/lib/opportunities/dates";

export type CalendarMode = "month" | "week";
export const CALENDAR_MODES = ["month", "week"] as const;

export const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
export const MONTH_LABELS = [
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
] as const;

/** A civil date, 1-based month, with no zone and no time. */
export type CivilDate = { year: number; month: number; day: number };

export type GridDay = {
  /** "YYYY-MM-DD" — the key events are bucketed under. */
  key: string;
  year: number;
  month: number;
  day: number;
  /** 0 = Sunday. */
  weekday: number;
  /** False for the leading/trailing days of the neighbouring months. */
  inMonth: boolean;
  isToday: boolean;
  isWeekend: boolean;
};

export type CalendarGrid = {
  mode: CalendarMode;
  /** "October 2026" or "Oct 12 – 18, 2026". */
  title: string;
  /** The anchor the grid was built around (the 1st for a month). */
  anchor: CivilDate;
  weeks: GridDay[][];
  days: GridDay[];
};

const DAY_MS = 86_400_000;

export function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** "2026-10-14" from a civil date. */
export function civilKey(date: CivilDate): string {
  return `${date.year}-${pad2(date.month)}-${pad2(date.day)}`;
}

/** Parses "2026-10-14"; null when the text is not a real calendar date. */
export function parseCivil(value: string | null | undefined): CivilDate | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value.trim());
  if (!match) return null;
  const [, y, m, d] = match.map(Number);
  const probe = new Date(Date.UTC(y, m - 1, d));
  if (Number.isNaN(probe.getTime())) return null;
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== m - 1 || probe.getUTCDate() !== d) return null;
  return { year: y, month: m, day: d };
}

const toUtc = (date: CivilDate) => Date.UTC(date.year, date.month - 1, date.day);

const fromUtc = (ms: number): CivilDate => {
  const d = new Date(ms);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
};

/** 0 = Sunday … 6 = Saturday. */
export function weekdayOf(date: CivilDate): number {
  return new Date(toUtc(date)).getUTCDay();
}

export function addDays(date: CivilDate, days: number): CivilDate {
  return fromUtc(toUtc(date) + days * DAY_MS);
}

/** Days in a month, leap years included. */
export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** Month arithmetic that never overflows: Jan 31 + 1 month is Feb 28/29. */
export function addMonths(date: CivilDate, months: number): CivilDate {
  const total = date.year * 12 + (date.month - 1) + months;
  const year = Math.floor(total / 12);
  const month = (total % 12) + 1;
  return { year, month, day: Math.min(date.day, daysInMonth(year, month)) };
}

/** The Sunday on or before `date`. */
export function startOfWeek(date: CivilDate): CivilDate {
  return addDays(date, -weekdayOf(date));
}

/** The viewer's civil date for an instant: which cell the event belongs in. */
export function dayKey(instant: Date | string, tz: string): string {
  const date = instant instanceof Date ? instant : new Date(instant);
  if (Number.isNaN(date.getTime())) return "";
  const parts = zoneParts(date, tz);
  return `${parts.year}-${pad2(parts.month)}-${pad2(parts.day)}`;
}

/** "today" as a civil date in `tz`. */
export function todayIn(tz: string, now: Date = new Date()): CivilDate {
  const parts = zoneParts(now, tz);
  return { year: parts.year, month: parts.month, day: parts.day };
}

function makeDay(date: CivilDate, month: number, todayKey: string): GridDay {
  const weekday = weekdayOf(date);
  const key = civilKey(date);
  return {
    key,
    year: date.year,
    month: date.month,
    day: date.day,
    weekday,
    inMonth: date.month === month,
    isToday: key === todayKey,
    isWeekend: weekday === 0 || weekday === 6,
  };
}

function chunk(days: GridDay[]): GridDay[][] {
  const weeks: GridDay[][] = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));
  return weeks;
}

export type GridOptions = { today?: CivilDate | null };

/**
 * A month as whole weeks, Sunday-first: the row containing the 1st through the
 * row containing the last day. February 2027 starts on a Monday and ends on a
 * Sunday, so it is exactly five rows; a 31-day month starting on Saturday
 * spills to six.
 */
export function monthGrid(anchor: CivilDate, options: GridOptions = {}): CalendarGrid {
  const first: CivilDate = { year: anchor.year, month: anchor.month, day: 1 };
  const last: CivilDate = { year: anchor.year, month: anchor.month, day: daysInMonth(anchor.year, anchor.month) };
  const start = startOfWeek(first);
  const end = addDays(startOfWeek(last), 6);
  const total = Math.round((toUtc(end) - toUtc(start)) / DAY_MS) + 1;
  const todayKey = options.today ? civilKey(options.today) : "";
  const days = Array.from({ length: total }, (_, i) => makeDay(addDays(start, i), anchor.month, todayKey));
  return {
    mode: "month",
    title: `${MONTH_LABELS[anchor.month - 1]} ${anchor.year}`,
    anchor: first,
    weeks: chunk(days),
    days,
  };
}

/** The Sunday-to-Saturday week containing `anchor`. */
export function weekGrid(anchor: CivilDate, options: GridOptions = {}): CalendarGrid {
  const start = startOfWeek(anchor);
  const todayKey = options.today ? civilKey(options.today) : "";
  const days = Array.from({ length: 7 }, (_, i) => {
    const date = addDays(start, i);
    return { ...makeDay(date, date.month, todayKey), inMonth: true };
  });
  const end = days[6];
  const startLabel = `${MONTH_LABELS[start.month - 1].slice(0, 3)} ${start.day}`;
  const endLabel =
    start.month === end.month
      ? `${end.day}`
      : `${MONTH_LABELS[end.month - 1].slice(0, 3)} ${end.day}`;
  const year = start.year === end.year ? `${end.year}` : `${start.year}–${end.year}`;
  return {
    mode: "week",
    title: `${startLabel} – ${endLabel}, ${year}`,
    anchor: start,
    weeks: [days],
    days,
  };
}

export function buildGrid(mode: CalendarMode, anchor: CivilDate, options: GridOptions = {}): CalendarGrid {
  return mode === "week" ? weekGrid(anchor, options) : monthGrid(anchor, options);
}

/** One step forward/back: a month for the month view, seven days for the week. */
export function shiftAnchor(mode: CalendarMode, anchor: CivilDate, delta: number): CivilDate {
  return mode === "week" ? addDays(anchor, delta * 7) : addMonths({ ...anchor, day: 1 }, delta);
}

/** Buckets anything with an instant under the viewer's civil day. */
export function groupByDay<T>(
  items: readonly T[],
  tz: string,
  instantOf: (item: T) => Date | string | null | undefined,
): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const instant = instantOf(item);
    if (!instant) continue;
    const key = dayKey(instant, tz);
    if (!key) continue;
    const bucket = out.get(key);
    if (bucket) bucket.push(item);
    else out.set(key, [item]);
  }
  return out;
}
