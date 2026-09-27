import { describe, expect, it } from "vitest";

import {
  barPercents,
  currentPeriod,
  formatDuration,
  formatPeriod,
  formatTimestamp,
  formatTokens,
  formatUsd,
  healthLabel,
  healthTone,
  isPeriod,
  recentPeriods,
  relativeTime,
  shiftPeriod,
} from "./format";

describe("formatUsd", () => {
  it("converts micro-dollars and keeps small amounts readable", () => {
    expect(formatUsd(2_500_000)).toBe("$2.50");
    expect(formatUsd(1_234_567_000)).toBe("$1,234.57");
    expect(formatUsd(810_000)).toBe("$0.81");
    expect(formatUsd(5_000)).toBe("$0.0050");
    expect(formatUsd(1)).toBe("$0.000001");
  });

  it("is safe with nothing to show", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(null)).toBe("$0.00");
    expect(formatUsd(undefined)).toBe("$0.00");
    expect(formatUsd(Number.NaN)).toBe("$0.00");
  });
});

describe("formatTokens", () => {
  it("is compact above a thousand", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(940)).toBe("940");
    expect(formatTokens(1500)).toBe("1.5K");
    expect(formatTokens(12_000)).toBe("12K");
    expect(formatTokens(12_300_000)).toBe("12.3M");
    expect(formatTokens(4_000_000_000)).toBe("4B");
    expect(formatTokens(null)).toBe("0");
  });
});

describe("periods", () => {
  it("recognises YYYY-MM only", () => {
    expect(isPeriod("2026-04")).toBe(true);
    expect(isPeriod("2026-13")).toBe(false);
    expect(isPeriod("2026-00")).toBe(false);
    expect(isPeriod("2026-4")).toBe(false);
  });

  it("reads the current month in UTC", () => {
    expect(currentPeriod(new Date("2026-04-12T10:00:00Z"))).toBe("2026-04");
    // 1 Jan 02:00 IST is still December in UTC, which is the period the API uses
    expect(currentPeriod(new Date("2026-12-31T23:30:00Z"))).toBe("2026-12");
  });

  it("shifts across year boundaries", () => {
    expect(shiftPeriod("2026-01", -1)).toBe("2025-12");
    expect(shiftPeriod("2026-12", 1)).toBe("2027-01");
    expect(shiftPeriod("2026-06", 0)).toBe("2026-06");
    expect(() => shiftPeriod("nope", 1)).toThrow(/YYYY-MM/);
  });

  it("lists recent months newest first", () => {
    expect(recentPeriods(3, "2026-02")).toEqual(["2026-02", "2026-01", "2025-12"]);
    expect(recentPeriods(1, "2026-02")).toEqual(["2026-02"]);
  });

  it("labels a period", () => {
    expect(formatPeriod("2026-04")).toBe("April 2026");
    expect(formatPeriod("2026-12")).toBe("December 2026");
    expect(formatPeriod("whatever")).toBe("whatever");
  });
});

describe("health", () => {
  it("maps a status to a tone", () => {
    expect(healthTone("ok")).toBe("ok");
    expect(healthTone("degraded")).toBe("warn");
    expect(healthTone("unconfigured")).toBe("warn");
    expect(healthTone("failing")).toBe("bad");
    expect(healthTone("not_implemented")).toBe("muted");
    expect(healthTone(null)).toBe("muted");
  });

  it("labels a status", () => {
    expect(healthLabel("not_implemented")).toBe("Not Implemented");
    expect(healthLabel("ok")).toBe("Ok");
    expect(healthLabel(null)).toBe("Unknown");
  });
});

describe("times", () => {
  it("formats an absolute timestamp", () => {
    expect(formatTimestamp("2026-04-14T09:12:00Z", "UTC")).toBe("14 Apr 2026, 09:12");
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp("not a date")).toBe("—");
  });

  it("formats a relative time", () => {
    const now = new Date("2026-04-14T12:00:00Z");
    expect(relativeTime("2026-04-14T11:59:30Z", now)).toBe("just now");
    expect(relativeTime("2026-04-14T11:45:00Z", now)).toBe("15m ago");
    expect(relativeTime("2026-04-14T09:00:00Z", now)).toBe("3h ago");
    expect(relativeTime("2026-04-09T12:00:00Z", now)).toBe("5d ago");
    expect(relativeTime("2026-04-14T12:30:00Z", now)).toBe("in the future");
    expect(relativeTime(null, now)).toBe("—");
  });

  it("formats a run duration", () => {
    expect(formatDuration("2026-04-14T12:00:00Z", "2026-04-14T12:00:42Z")).toBe("42s");
    expect(formatDuration("2026-04-14T12:00:00Z", "2026-04-14T12:01:05Z")).toBe("1m 05s");
    expect(formatDuration("2026-04-14T12:00:00Z", null)).toBe("—");
    expect(formatDuration(null, "2026-04-14T12:00:00Z")).toBe("—");
  });
});

describe("barPercents", () => {
  it("scales against the largest value", () => {
    expect(barPercents([10, 5, 0])).toEqual([100, 50, 0]);
    expect(barPercents([0, 0])).toEqual([0, 0]);
    expect(barPercents([])).toEqual([]);
  });

  it("keeps a sliver visible for tiny non-zero values", () => {
    expect(barPercents([1000, 1])).toEqual([100, 1]);
  });
});
