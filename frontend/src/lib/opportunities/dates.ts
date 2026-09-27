/**
 * Dual time zone rendering on the client, mirroring backend core.dates.dual_tz
 * and core.display_time.countdown (M3-01 / M6-10, OQ-24).
 *
 * The API may send a date either as a plain ISO string (with the record's
 * `source_tz` alongside) or as a `TzDateOut` object that already carries the
 * buyer's and the user's renderings. Both shapes go through `dualTz`, which
 * prefers the server's strings when present and otherwise formats with Intl
 * in the buyer's zone and the browser's zone:
 *
 *   same local date:   "Oct 14, 2:00 PM EDT = 11:30 PM IST"
 *   overnight:         "Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST"
 *   same zone:         "Oct 14, 2:00 PM EDT"
 */

/** Wire shape decided in OQ-24 (core.display_time.TzDateOut). */
export type TzDateOut = {
  utc: string;
  buyer_tz: string;
  buyer_local: string;
  buyer_display: string;
  user_tz?: string | null;
  user_local?: string | null;
  user_display?: string | null;
  display: string;
};

export type TzDateInput = string | TzDateOut | null | undefined;

export type DualTime = {
  utc: Date;
  buyerTz: string;
  userTz: string | null;
  /** "Oct 14, 2:00 PM EDT" */
  buyer: string;
  /** "11:30 PM IST" or "Oct 15, 2:30 AM IST"; null when it would repeat the buyer's. */
  user: string | null;
  /** Buyer and user joined with " = ", or the buyer alone. */
  display: string;
};

export function isTzDateOut(value: unknown): value is TzDateOut {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as TzDateOut).utc === "string" &&
    typeof (value as TzDateOut).buyer_tz === "string"
  );
}

/** The instant behind either wire shape, or null when absent/unparseable. */
export function toDate(value: TzDateInput): Date | null {
  if (!value) return null;
  const iso = isTzDateOut(value) ? value.utc : value;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** The viewer's IANA zone; UTC when the runtime cannot tell. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Abbreviations where ICU's en-US short name is a bare offset. */
const ZONE_ABBREVIATIONS: Record<string, string> = {
  "Asia/Kolkata": "IST",
  "Asia/Calcutta": "IST",
  UTC: "UTC",
  Etc: "UTC",
  "Etc/UTC": "UTC",
};

export type ZoneParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  /** "EDT", "IST", or "GMT+5:30" when ICU has no abbreviation. */
  abbreviation: string;
  /** Offset from UTC in minutes (east positive). */
  offsetMinutes: number;
  monthShort: string;
};

function isValidZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** Wall-clock parts of `date` in `tz`; falls back to UTC for an unknown zone. */
export function zoneParts(date: Date, tz: string): ZoneParts {
  const zone = isValidZone(tz) ? tz : "UTC";
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    hourCycle: "h23",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
  const parts = Object.fromEntries(formatter.formatToParts(date).map((p) => [p.type, p.value])) as Record<
    string,
    string
  >;
  const year = Number(parts.year);
  const monthShort = parts.month;
  const month = MONTHS.indexOf(monthShort) + 1;
  const day = Number(parts.day);
  const hour = Number(parts.hour) % 24;
  const minute = Number(parts.minute);
  const asUtc = Date.UTC(year, month - 1, day, hour, minute, date.getUTCSeconds());
  const offsetMinutes = Math.round((asUtc - date.getTime()) / 60000);
  const raw = parts.timeZoneName ?? "";
  const abbreviation =
    ZONE_ABBREVIATIONS[zone] ?? (raw && !/^GMT[+-]?\d/.test(raw) ? raw : raw || "UTC");
  return { year, month, day, hour, minute, abbreviation, offsetMinutes, monthShort };
}

function clock(parts: ZoneParts): string {
  const hour12 = parts.hour % 12 || 12;
  const meridiem = parts.hour < 12 ? "AM" : "PM";
  return `${hour12}:${String(parts.minute).padStart(2, "0")} ${meridiem} ${parts.abbreviation}`;
}

function dayText(parts: ZoneParts, withYear: boolean): string {
  const text = `${parts.monthShort} ${parts.day}`;
  return withYear ? `${text}, ${parts.year}` : text;
}

export type FormatOptions = { withDate?: boolean; withYear?: boolean };

/** "Oct 14, 2:00 PM EDT" (withYear: "Oct 14, 2026, 2:00 PM EDT"; withDate false: "2:00 PM EDT"). */
export function formatInZone(date: Date, tz: string, options: FormatOptions = {}): string {
  const { withDate = true, withYear = false } = options;
  const parts = zoneParts(date, tz);
  return withDate ? `${dayText(parts, withYear)}, ${clock(parts)}` : clock(parts);
}

/**
 * Buyer-zone rendering with the user's zone alongside. `userTz` defaults to the
 * browser zone; pass null to render the buyer's zone alone.
 */
export function dualTz(
  value: TzDateInput,
  buyerTz: string | null | undefined,
  userTz: string | null | undefined = browserTimeZone(),
  options: { withYear?: boolean } = {},
): DualTime | null {
  const utc = toDate(value);
  if (!utc) return null;
  const withYear = options.withYear ?? false;

  if (isTzDateOut(value) && !withYear) {
    // Server strings win when the user zone is the one the server knew about.
    const serverUser = value.user_tz ?? null;
    if (serverUser && (!userTz || userTz === serverUser)) {
      const user = value.user_display && value.display !== value.buyer_display ? stripBuyerDate(value) : null;
      return {
        utc,
        buyerTz: value.buyer_tz,
        userTz: serverUser,
        buyer: value.buyer_display,
        user,
        display: value.display,
      };
    }
    buyerTz = value.buyer_tz;
  }

  const zone = buyerTz && isValidZone(buyerTz) ? buyerTz : "UTC";
  const buyerParts = zoneParts(utc, zone);
  const buyer = `${dayText(buyerParts, withYear)}, ${clock(buyerParts)}`;
  if (!userTz || !isValidZone(userTz)) {
    return { utc, buyerTz: zone, userTz: null, buyer, user: null, display: buyer };
  }
  const userParts = zoneParts(utc, userTz);
  const sameZone =
    userParts.offsetMinutes === buyerParts.offsetMinutes && userParts.abbreviation === buyerParts.abbreviation;
  if (sameZone) {
    return { utc, buyerTz: zone, userTz, buyer, user: null, display: buyer };
  }
  const sameDate =
    userParts.year === buyerParts.year && userParts.month === buyerParts.month && userParts.day === buyerParts.day;
  const user = sameDate ? clock(userParts) : `${dayText(userParts, withYear)}, ${clock(userParts)}`;
  return { utc, buyerTz: zone, userTz, buyer, user, display: `${buyer} = ${user}` };
}

/** The user half of a server `display` ("… = 11:30 PM IST" -> "11:30 PM IST"). */
function stripBuyerDate(value: TzDateOut): string | null {
  const index = value.display.indexOf(" = ");
  if (index === -1) return value.user_display ?? null;
  return value.display.slice(index + 3);
}

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;

function humanize(seconds: number): string {
  const days = Math.floor(seconds / DAY);
  const hours = Math.floor((seconds % DAY) / HOUR);
  const minutes = Math.floor((seconds % HOUR) / MINUTE);
  if (days) return hours ? `${days}d ${hours}h` : `${days}d`;
  if (hours) return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  return `${minutes}m`;
}

/**
 * Humanized time until `due`: "3d 4h", "6h 12m", "45m"; "due now" within a
 * minute either way; "overdue 2h" once past. Floors to the two largest units.
 */
export function countdown(now: Date, due: Date): string {
  const remaining = Math.trunc((due.getTime() - now.getTime()) / 1000);
  if (Math.abs(remaining) < MINUTE) return "due now";
  if (remaining < 0) return `overdue ${humanize(-remaining)}`;
  return humanize(remaining);
}

export type CountdownTone = "overdue" | "urgent" | "soon" | "normal";

/** Urgency band for the countdown badge: < 72 h urgent (SPEC 7 quiet-hours cut-off), < 7 d soon. */
export function countdownTone(now: Date, due: Date): CountdownTone {
  const remaining = due.getTime() - now.getTime();
  if (remaining < -MINUTE * 1000) return "overdue";
  if (remaining < 72 * HOUR * 1000) return "urgent";
  if (remaining < 7 * DAY * 1000) return "soon";
  return "normal";
}

/** "in 3d 4h" / "overdue 2h" / "due now" for prose. */
export function countdownPhrase(now: Date, due: Date): string {
  const text = countdown(now, due);
  return text.startsWith("overdue") || text === "due now" ? text : `in ${text}`;
}
