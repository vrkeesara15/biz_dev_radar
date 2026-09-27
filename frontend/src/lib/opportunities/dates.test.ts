import { describe, expect, it } from "vitest";

import {
  countdown,
  countdownPhrase,
  countdownTone,
  dualTz,
  formatInZone,
  isTzDateOut,
  toDate,
  zoneParts,
  type TzDateOut,
} from "./dates";

// 2026-10-14 18:00 UTC = 2:00 PM EDT = 11:30 PM IST (the SPEC 9 example).
const SPEC_EXAMPLE = "2026-10-14T18:00:00Z";

describe("zoneParts / formatInZone", () => {
  it("renders the SPEC 9 example in the buyer's zone", () => {
    expect(formatInZone(new Date(SPEC_EXAMPLE), "America/New_York")).toBe("Oct 14, 2:00 PM EDT");
    expect(formatInZone(new Date(SPEC_EXAMPLE), "America/New_York", { withYear: true })).toBe(
      "Oct 14, 2026, 2:00 PM EDT",
    );
    expect(formatInZone(new Date(SPEC_EXAMPLE), "America/New_York", { withDate: false })).toBe("2:00 PM EDT");
  });

  it("uses IST for Asia/Kolkata even though ICU only knows the offset", () => {
    const parts = zoneParts(new Date(SPEC_EXAMPLE), "Asia/Kolkata");
    expect(parts.abbreviation).toBe("IST");
    expect(parts.offsetMinutes).toBe(330);
    expect(parts.hour).toBe(23);
    expect(parts.minute).toBe(30);
  });

  it("switches EDT to EST across the fall-back boundary", () => {
    expect(formatInZone(new Date("2026-11-01T05:30:00Z"), "America/New_York")).toBe("Nov 1, 1:30 AM EDT");
    expect(formatInZone(new Date("2026-11-01T06:30:00Z"), "America/New_York")).toBe("Nov 1, 1:30 AM EST");
  });

  it("falls back to UTC for an unknown zone", () => {
    expect(formatInZone(new Date(SPEC_EXAMPLE), "Mars/Olympus")).toBe("Oct 14, 6:00 PM UTC");
  });

  it("renders midnight and noon with 12-hour clocks", () => {
    expect(formatInZone(new Date("2026-03-05T00:00:00Z"), "UTC")).toBe("Mar 5, 12:00 AM UTC");
    expect(formatInZone(new Date("2026-03-05T12:00:00Z"), "UTC")).toBe("Mar 5, 12:00 PM UTC");
  });
});

describe("dualTz with ISO strings", () => {
  it("shows the buyer's zone with the user's alongside", () => {
    const out = dualTz(SPEC_EXAMPLE, "America/New_York", "Asia/Kolkata");
    expect(out?.display).toBe("Oct 14, 2:00 PM EDT = 11:30 PM IST");
    expect(out?.buyer).toBe("Oct 14, 2:00 PM EDT");
    expect(out?.user).toBe("11:30 PM IST");
  });

  it("repeats the date when the user's local date differs (overnight)", () => {
    expect(dualTz("2026-10-14T21:00:00Z", "America/New_York", "Asia/Kolkata")?.display).toBe(
      "Oct 14, 5:00 PM EDT = Oct 15, 2:30 AM IST",
    );
    // and the other way round for an Indian buyer with a US reader
    expect(dualTz("2026-10-14T02:30:00Z", "Asia/Kolkata", "America/New_York")?.display).toBe(
      "Oct 14, 8:00 AM IST = Oct 13, 10:30 PM EDT",
    );
    // same calendar date in both zones: the date is not repeated
    expect(dualTz("2026-10-14T04:30:00Z", "Asia/Kolkata", "America/New_York")?.display).toBe(
      "Oct 14, 10:00 AM IST = 12:30 AM EDT",
    );
  });

  it("collapses to a single rendering when both zones agree, or without a user zone", () => {
    expect(dualTz(SPEC_EXAMPLE, "America/New_York", "America/Detroit")?.display).toBe("Oct 14, 2:00 PM EDT");
    expect(dualTz(SPEC_EXAMPLE, "America/New_York", null)?.display).toBe("Oct 14, 2:00 PM EDT");
    expect(dualTz(SPEC_EXAMPLE, "America/New_York", null)?.user).toBeNull();
  });

  it("returns null for missing or garbage values", () => {
    expect(dualTz(null, "UTC", "UTC")).toBeNull();
    expect(dualTz("not a date", "UTC", "UTC")).toBeNull();
    expect(toDate(undefined)).toBeNull();
  });
});

describe("dualTz with TzDateOut objects (OQ-24)", () => {
  const wire: TzDateOut = {
    utc: SPEC_EXAMPLE,
    buyer_tz: "America/New_York",
    buyer_local: "2026-10-14T14:00:00-04:00",
    buyer_display: "Oct 14, 2:00 PM EDT",
    user_tz: "Asia/Kolkata",
    user_local: "2026-10-14T23:30:00+05:30",
    user_display: "Oct 14, 11:30 PM IST",
    display: "Oct 14, 2:00 PM EDT = 11:30 PM IST",
  };

  it("recognises the wire shape and prefers the server strings", () => {
    expect(isTzDateOut(wire)).toBe(true);
    expect(isTzDateOut(SPEC_EXAMPLE)).toBe(false);
    const out = dualTz(wire, null, "Asia/Kolkata");
    expect(out?.display).toBe(wire.display);
    expect(out?.buyer).toBe("Oct 14, 2:00 PM EDT");
    expect(out?.user).toBe("11:30 PM IST");
    expect(out?.userTz).toBe("Asia/Kolkata");
  });

  it("re-renders the user side when the browser zone differs from the server's user zone", () => {
    expect(dualTz(wire, null, "Europe/London")?.display).toBe("Oct 14, 2:00 PM EDT = 7:00 PM GMT+1");
  });

  it("falls back to the browser zone when the server knew no user zone", () => {
    const anonymous: TzDateOut = { ...wire, user_tz: null, user_local: null, user_display: null, display: wire.buyer_display };
    expect(dualTz(anonymous, null, "Asia/Kolkata")?.display).toBe("Oct 14, 2:00 PM EDT = 11:30 PM IST");
  });
});

describe("countdown", () => {
  const now = new Date("2026-10-11T10:00:00Z");
  const at = (iso: string) => new Date(iso);

  it("floors to the two largest units like the backend helper", () => {
    expect(countdown(now, at("2026-10-14T14:30:00Z"))).toBe("3d 4h");
    expect(countdown(now, at("2026-10-11T16:12:30Z"))).toBe("6h 12m");
    expect(countdown(now, at("2026-10-11T10:45:00Z"))).toBe("45m");
    expect(countdown(now, at("2026-10-14T10:00:00Z"))).toBe("3d");
    expect(countdown(now, at("2026-10-11T12:00:00Z"))).toBe("2h");
  });

  it("reports due now within a minute either way and overdue afterwards", () => {
    expect(countdown(now, at("2026-10-11T10:00:30Z"))).toBe("due now");
    expect(countdown(now, at("2026-10-11T09:59:31Z"))).toBe("due now");
    expect(countdown(now, at("2026-10-10T07:00:00Z"))).toBe("overdue 1d 3h");
    expect(countdown(now, at("2026-10-11T08:00:00Z"))).toBe("overdue 2h");
  });

  it("phrases and tones the badge", () => {
    expect(countdownPhrase(now, at("2026-10-14T14:30:00Z"))).toBe("in 3d 4h");
    expect(countdownPhrase(now, at("2026-10-10T07:00:00Z"))).toBe("overdue 1d 3h");
    expect(countdownPhrase(now, now)).toBe("due now");
    expect(countdownTone(now, at("2026-10-10T07:00:00Z"))).toBe("overdue");
    expect(countdownTone(now, at("2026-10-12T10:00:00Z"))).toBe("urgent");
    expect(countdownTone(now, at("2026-10-15T10:00:00Z"))).toBe("soon");
    expect(countdownTone(now, at("2026-11-15T10:00:00Z"))).toBe("normal");
  });
});
