import { describe, expect, it } from "vitest";

import {
  FALLBACK_TIME_ZONES,
  browserTimeZone,
  filterTimeZones,
  isValidTimeZone,
  timeZoneLabel,
  timeZoneOffset,
  timeZones,
} from "./timezones";

const ZONES = [...FALLBACK_TIME_ZONES];

describe("timeZones", () => {
  it("lists UTC first and never repeats a zone", () => {
    const list = timeZones();
    expect(list[0]).toBe("UTC");
    expect(new Set(list).size).toBe(list.length);
    expect(list).toContain("Asia/Kolkata");
    expect(list).toContain("America/New_York");
  });
});

describe("filterTimeZones", () => {
  it("returns everything for an empty query", () => {
    expect(filterTimeZones("", ZONES, 0)).toEqual(ZONES);
  });

  it("matches the city regardless of case", () => {
    expect(filterTimeZones("kolkata", ZONES, 0)).toEqual(["Asia/Kolkata"]);
    expect(filterTimeZones("KOLKATA", ZONES, 0)).toEqual(["Asia/Kolkata"]);
  });

  it("matches the underscore form typed with a space", () => {
    expect(filterTimeZones("new york", ZONES, 0)).toEqual(["America/New_York"]);
    expect(filterTimeZones("los angeles", ZONES, 0)).toEqual(["America/Los_Angeles"]);
  });

  it("finds a zone by its country or abbreviation", () => {
    expect(filterTimeZones("india", ZONES, 0)).toEqual(["Asia/Kolkata"]);
    expect(filterTimeZones("ist", ZONES, 0)).toEqual(["Asia/Kolkata"]);
    expect(filterTimeZones("pacific", ZONES, 0)).toContain("America/Los_Angeles");
  });

  it("requires every term to match", () => {
    expect(filterTimeZones("america new", ZONES, 0)).toEqual(["America/New_York"]);
    expect(filterTimeZones("asia york", ZONES, 0)).toEqual([]);
  });

  it("treats the separator characters as term breaks", () => {
    expect(filterTimeZones("asia/kolkata", ZONES, 0)).toEqual(["Asia/Kolkata"]);
    expect(filterTimeZones("america_denver", ZONES, 0)).toEqual(["America/Denver"]);
  });

  it("honours the limit", () => {
    expect(filterTimeZones("a", ZONES, 3)).toHaveLength(3);
    expect(filterTimeZones("zzzz", ZONES, 0)).toEqual([]);
  });
});

describe("labels and offsets", () => {
  it("spaces the zone id out for display", () => {
    expect(timeZoneLabel("America/New_York")).toBe("America / New York");
    expect(timeZoneLabel("UTC")).toBe("UTC");
  });

  it("reports the current offset", () => {
    expect(timeZoneOffset("Asia/Kolkata", new Date("2026-01-15T00:00:00Z"))).toBe("+05:30");
    expect(timeZoneOffset("UTC", new Date("2026-01-15T00:00:00Z"))).toBe("+00:00");
    expect(timeZoneOffset("Not/AZone")).toBe("");
  });

  it("validates zone ids", () => {
    expect(isValidTimeZone("Asia/Kolkata")).toBe(true);
    expect(isValidTimeZone("Mars/Olympus")).toBe(false);
    expect(isValidTimeZone(" ")).toBe(false);
  });

  it("reads the browser zone", () => {
    expect(isValidTimeZone(browserTimeZone())).toBe(true);
  });
});
