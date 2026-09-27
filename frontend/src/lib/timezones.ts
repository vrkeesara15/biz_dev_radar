/**
 * IANA time-zone list and search, for the notification-preferences picker.
 *
 * `Intl.supportedValuesOf("timeZone")` is the browser's own tzdb (hundreds of
 * zones); where it is missing we fall back to a short list that still covers
 * both deployment regions. Matching is accent-insensitive on the zone id and on
 * its human form ("Asia/Kolkata" is found by "kolkata", "india" and "IST").
 */

/** Used when the runtime has no `Intl.supportedValuesOf`. */
export const FALLBACK_TIME_ZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "America/Puerto_Rico",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Berlin",
  "Europe/Paris",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
] as const;

/** Extra words that should find a zone: the abbreviation and the country. */
const ALIASES: Record<string, string> = {
  "Asia/Kolkata": "ist india indian standard time calcutta bengaluru mumbai delhi",
  "Asia/Calcutta": "ist india indian standard time kolkata",
  "America/New_York": "est edt eastern us united states",
  "America/Chicago": "cst cdt central us united states",
  "America/Denver": "mst mdt mountain us united states",
  "America/Phoenix": "mst arizona us united states",
  "America/Los_Angeles": "pst pdt pacific us united states california",
  "America/Anchorage": "akst alaska us united states",
  "Pacific/Honolulu": "hst hawaii us united states",
  "Europe/London": "gmt bst united kingdom uk",
  UTC: "gmt utc coordinated universal time",
};

/**
 * Some ICU builds still report the legacy link rather than the canonical zone
 * (Node returns "Asia/Calcutta"); the picker shows the canonical name, which
 * `zoneinfo` on the backend accepts either way.
 */
export const CANONICAL: Record<string, string> = {
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Rangoon": "Asia/Yangon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Europe/Kiev": "Europe/Kyiv",
  "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
};

export const canonicalTimeZone = (zone: string): string => CANONICAL[zone] ?? zone;

let cache: string[] | null = null;

/** Every zone the runtime knows, sorted, with UTC first. */
export function timeZones(): string[] {
  if (cache) return cache;
  let list: string[];
  try {
    const supported = (
      Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
    ).supportedValuesOf?.("timeZone");
    list = Array.isArray(supported) && supported.length ? [...supported] : [...FALLBACK_TIME_ZONES];
  } catch {
    list = [...FALLBACK_TIME_ZONES];
  }
  const unique = Array.from(
    new Set(["UTC", ...list.map(canonicalTimeZone), ...FALLBACK_TIME_ZONES]),
  );
  unique.sort((a, b) => (a === "UTC" ? -1 : b === "UTC" ? 1 : a.localeCompare(b)));
  cache = unique;
  return cache;
}

/** The browser's zone, or UTC where it cannot be read. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** "Asia/Kolkata" -> "Asia / Kolkata" for display. */
export function timeZoneLabel(zone: string): string {
  return zone.replace(/_/g, " ").replace(/\//g, " / ");
}

/** Current UTC offset as "+05:30", or "" when the zone is not resolvable. */
export function timeZoneOffset(zone: string, now: Date = new Date()): string {
  try {
    const formatted = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      timeZoneName: "longOffset",
    }).formatToParts(now);
    const name = formatted.find((part) => part.type === "timeZoneName")?.value ?? "";
    if (name === "GMT") return "+00:00";
    return name.replace(/^GMT/, "") || "";
  } catch {
    return "";
  }
}

const normalize = (value: string) =>
  value
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/**
 * Zones matching every whitespace-separated term of `query` (id, human form or
 * alias). An empty query returns the whole list. `limit` keeps the rendered
 * option list short; 0 means no limit.
 */
export function filterTimeZones(query: string, zones: string[] = timeZones(), limit = 100): string[] {
  const terms = normalize(query).split(/[\s,/_-]+/).filter(Boolean);
  const matches = !terms.length
    ? zones
    : zones.filter((zone) => {
        const haystack = `${normalize(zone)} ${normalize(zone.replace(/[_/]/g, " "))} ${
          ALIASES[zone] ?? ""
        }`;
        return terms.every((term) => haystack.includes(term));
      });
  return limit > 0 ? matches.slice(0, limit) : matches;
}

/** True when the runtime accepts `zone` as an IANA identifier. */
export function isValidTimeZone(zone: string): boolean {
  if (!zone.trim()) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}
