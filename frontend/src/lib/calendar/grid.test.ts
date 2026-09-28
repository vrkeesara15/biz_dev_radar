import { describe, expect, it } from "vitest";

import {
  addDays,
  addMonths,
  buildGrid,
  civilKey,
  dayKey,
  daysInMonth,
  groupByDay,
  monthGrid,
  parseCivil,
  shiftAnchor,
  startOfWeek,
  todayIn,
  weekGrid,
  weekdayOf,
} from "./grid";

describe("civil date arithmetic", () => {
  it("parses only real calendar dates", () => {
    expect(parseCivil("2026-10-14")).toEqual({ year: 2026, month: 10, day: 14 });
    expect(parseCivil("2026-02-29")).toBeNull(); // 2026 is not a leap year
    expect(parseCivil("2024-02-29")).toEqual({ year: 2024, month: 2, day: 29 });
    expect(parseCivil("2026-13-01")).toBeNull();
    expect(parseCivil("nonsense")).toBeNull();
    expect(parseCivil(null)).toBeNull();
  });

  it("counts the days in a month, leap years included", () => {
    expect(daysInMonth(2026, 2)).toBe(28);
    expect(daysInMonth(2024, 2)).toBe(29);
    expect(daysInMonth(2026, 10)).toBe(31);
    expect(daysInMonth(2026, 11)).toBe(30);
  });

  it("adds days across a month and a year boundary", () => {
    expect(addDays({ year: 2026, month: 10, day: 31 }, 1)).toEqual({ year: 2026, month: 11, day: 1 });
    expect(addDays({ year: 2026, month: 1, day: 1 }, -1)).toEqual({ year: 2025, month: 12, day: 31 });
  });

  it("adds months without overflowing into the next one", () => {
    expect(addMonths({ year: 2026, month: 1, day: 31 }, 1)).toEqual({ year: 2026, month: 2, day: 28 });
    expect(addMonths({ year: 2026, month: 12, day: 15 }, 1)).toEqual({ year: 2027, month: 1, day: 15 });
    expect(addMonths({ year: 2026, month: 1, day: 15 }, -1)).toEqual({ year: 2025, month: 12, day: 15 });
  });

  it("finds the Sunday on or before a date", () => {
    // 2026-10-14 is a Wednesday.
    expect(weekdayOf({ year: 2026, month: 10, day: 14 })).toBe(3);
    expect(startOfWeek({ year: 2026, month: 10, day: 14 })).toEqual({ year: 2026, month: 10, day: 11 });
    // A Sunday is its own week start.
    expect(startOfWeek({ year: 2026, month: 10, day: 11 })).toEqual({ year: 2026, month: 10, day: 11 });
  });
});

describe("monthGrid", () => {
  it("covers the whole month in whole Sunday-first weeks", () => {
    const grid = monthGrid({ year: 2026, month: 10, day: 1 });
    expect(grid.title).toBe("October 2026");
    expect(grid.days).toHaveLength(grid.weeks.length * 7);
    expect(grid.weeks.every((week) => week.length === 7)).toBe(true);
    expect(grid.days[0].weekday).toBe(0);
    expect(grid.days[grid.days.length - 1].weekday).toBe(6);
    // Every day of October is present exactly once.
    const inMonth = grid.days.filter((day) => day.inMonth).map((day) => day.day);
    expect(inMonth).toEqual(Array.from({ length: 31 }, (_, i) => i + 1));
  });

  it("pads the boundaries with the neighbouring months, marked out of month", () => {
    // October 2026 starts on a Thursday, so the first row leads with Sep 27–30;
    // it ends on Saturday the 31st, so there is nothing to pad at the tail.
    const grid = monthGrid({ year: 2026, month: 10, day: 1 });
    expect(grid.days.slice(0, 4).map((day) => day.key)).toEqual([
      "2026-09-27",
      "2026-09-28",
      "2026-09-29",
      "2026-09-30",
    ]);
    expect(grid.days.slice(0, 4).every((day) => !day.inMonth)).toBe(true);
    expect(grid.days[grid.days.length - 1].key).toBe("2026-10-31");

    // September 2026 ends on a Wednesday, so its last row is padded with Oct 1–3.
    const september = monthGrid({ year: 2026, month: 9, day: 1 });
    const tail = september.days.slice(-3);
    expect(tail.map((day) => day.key)).toEqual(["2026-10-01", "2026-10-02", "2026-10-03"]);
    expect(tail.every((day) => !day.inMonth)).toBe(true);
  });

  it("is exactly five rows for a month that fills them", () => {
    // February 2027: Monday the 1st through Sunday the 28th.
    const grid = monthGrid({ year: 2027, month: 2, day: 1 });
    expect(grid.weeks).toHaveLength(5);
    expect(grid.days[0].key).toBe("2027-01-31");
    expect(grid.days[grid.days.length - 1].key).toBe("2027-03-06");
  });

  it("is six rows for a 31-day month that starts on a Saturday", () => {
    // August 2026 starts on Saturday the 1st.
    const grid = monthGrid({ year: 2026, month: 8, day: 1 });
    expect(grid.weeks).toHaveLength(6);
  });

  it("crosses a year boundary cleanly", () => {
    const grid = monthGrid({ year: 2026, month: 12, day: 1 });
    expect(grid.title).toBe("December 2026");
    expect(grid.days.some((day) => day.key.startsWith("2027-01"))).toBe(true);
    expect(grid.days.filter((day) => day.inMonth)).toHaveLength(31);
  });

  it("marks today, and only today", () => {
    const grid = monthGrid({ year: 2026, month: 10, day: 1 }, { today: { year: 2026, month: 10, day: 14 } });
    expect(grid.days.filter((day) => day.isToday).map((day) => day.key)).toEqual(["2026-10-14"]);
  });
});

describe("weekGrid", () => {
  it("is one Sunday-to-Saturday row around the anchor", () => {
    const grid = weekGrid({ year: 2026, month: 10, day: 14 });
    expect(grid.weeks).toHaveLength(1);
    expect(grid.days.map((day) => day.key)).toEqual([
      "2026-10-11",
      "2026-10-12",
      "2026-10-13",
      "2026-10-14",
      "2026-10-15",
      "2026-10-16",
      "2026-10-17",
    ]);
    expect(grid.title).toBe("Oct 11 – 17, 2026");
    expect(grid.days.every((day) => day.inMonth)).toBe(true);
  });

  it("names both months when the week straddles them", () => {
    const grid = weekGrid({ year: 2026, month: 11, day: 1 });
    expect(grid.title).toBe("Nov 1 – 7, 2026");
    const crossing = weekGrid({ year: 2026, month: 10, day: 29 });
    expect(crossing.title).toBe("Oct 25 – 31, 2026");
    const yearEnd = weekGrid({ year: 2026, month: 12, day: 31 });
    expect(yearEnd.title).toBe("Dec 27 – Jan 2, 2026–2027");
  });
});

describe("navigation", () => {
  it("steps a month at a time in the month view", () => {
    expect(shiftAnchor("month", { year: 2026, month: 12, day: 31 }, 1)).toEqual({
      year: 2027,
      month: 1,
      day: 1,
    });
    expect(shiftAnchor("month", { year: 2026, month: 1, day: 15 }, -1)).toEqual({
      year: 2025,
      month: 12,
      day: 1,
    });
  });

  it("steps seven days at a time in the week view", () => {
    expect(shiftAnchor("week", { year: 2026, month: 10, day: 29 }, 1)).toEqual({
      year: 2026,
      month: 11,
      day: 5,
    });
  });

  it("buildGrid picks the right builder", () => {
    expect(buildGrid("week", { year: 2026, month: 10, day: 14 }).mode).toBe("week");
    expect(buildGrid("month", { year: 2026, month: 10, day: 14 }).weeks.length).toBeGreaterThan(1);
  });
});

describe("dayKey across time zones", () => {
  // 2026-10-14 21:00 UTC = 5:00 PM EDT (Oct 14) = 2:30 AM IST (Oct 15).
  const overnight = "2026-10-14T21:00:00Z";

  it("puts one instant on different days for an EDT and an IST reader", () => {
    expect(dayKey(overnight, "America/New_York")).toBe("2026-10-14");
    expect(dayKey(overnight, "Asia/Kolkata")).toBe("2026-10-15");
    expect(dayKey(overnight, "UTC")).toBe("2026-10-14");
  });

  it("handles the other side: an early-morning UTC instant is still yesterday in New York", () => {
    const early = "2026-10-15T02:00:00Z";
    expect(dayKey(early, "America/New_York")).toBe("2026-10-14");
    expect(dayKey(early, "Asia/Kolkata")).toBe("2026-10-15");
  });

  it("falls back to UTC for a zone the runtime does not know", () => {
    expect(dayKey(overnight, "Mars/Olympus")).toBe("2026-10-14");
  });

  it("answers empty for an unparseable instant instead of throwing", () => {
    expect(dayKey("not a date", "UTC")).toBe("");
  });

  it("reports today in the reader's zone", () => {
    const now = new Date(overnight);
    expect(civilKey(todayIn("Asia/Kolkata", now))).toBe("2026-10-15");
    expect(civilKey(todayIn("America/New_York", now))).toBe("2026-10-14");
  });
});

describe("groupByDay", () => {
  const events = [
    { id: "a", at: "2026-10-14T21:00:00Z" },
    { id: "b", at: "2026-10-14T12:00:00Z" },
    { id: "c", at: "2026-10-15T06:00:00Z" },
    { id: "d", at: null },
  ];

  it("buckets events under the reader's civil day", () => {
    const ist = groupByDay(events, "Asia/Kolkata", (e) => e.at);
    expect([...(ist.get("2026-10-14") ?? [])].map((e) => e.id)).toEqual(["b"]);
    expect([...(ist.get("2026-10-15") ?? [])].map((e) => e.id)).toEqual(["a", "c"]);

    const edt = groupByDay(events, "America/New_York", (e) => e.at);
    expect([...(edt.get("2026-10-14") ?? [])].map((e) => e.id)).toEqual(["a", "b"]);
    expect([...(edt.get("2026-10-15") ?? [])].map((e) => e.id)).toEqual(["c"]);
  });

  it("skips an event with no instant", () => {
    const all = [...groupByDay(events, "UTC", (e) => e.at).values()].flat();
    expect(all.map((e) => e.id)).not.toContain("d");
  });
});
