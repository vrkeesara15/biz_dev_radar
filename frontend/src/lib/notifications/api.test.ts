import { describe, expect, it } from "vitest";

import { eventTitle, relativeTime, toActionPath, toAppPath, toBellItem } from "./api";
import type { NotificationOut } from "./api";

const row = (payload: Record<string, unknown>, overrides: Partial<NotificationOut> = {}): NotificationOut => ({
  id: "n-1",
  event_type: "high_fit_match",
  opportunity_id: "opp-1",
  pursuit_id: null,
  version: 1,
  payload,
  created_at: "2026-09-27T10:00:00Z",
  read_at: null,
  ...overrides,
});

describe("toAppPath", () => {
  it("keeps only the path of a deep link minted with another base URL", () => {
    expect(toAppPath("https://api.example.com/app/opportunities/abc")).toBe("/app/opportunities/abc");
    expect(toAppPath("https://app.bidradar.test/app/pursuits/p1?tab=drafts")).toBe("/app/pursuits/p1?tab=drafts");
  });

  it("passes a relative link through and rejects nonsense", () => {
    expect(toAppPath("/app")).toBe("/app");
    expect(toAppPath("")).toBeNull();
    expect(toAppPath(null)).toBeNull();
    expect(toAppPath("not a url")).toBeNull();
  });
});

describe("toActionPath", () => {
  it("rewrites an absolute API action link onto this origin", () => {
    expect(toActionPath("https://api.example.com/api/v1/notifications/actions/tok123")).toBe(
      "/api/v1/notifications/actions/tok123",
    );
  });

  it("refuses a link that is not an API route", () => {
    expect(toActionPath("https://evil.example.com/steal")).toBeNull();
    expect(toActionPath("/app/opportunities/1")).toBeNull();
  });
});

describe("toBellItem", () => {
  it("reads title, detail, deep link, score and the one-click actions", () => {
    const item = toBellItem(
      row({
        title: "Cloud migration services",
        buyer: "Department of Energy",
        band: "High fit",
        score: 82.4,
        deep_link: "https://app.test/app/opportunities/opp-1",
        actions: {
          pursue: "https://api.test/api/v1/notifications/actions/a",
          pass: "https://api.test/api/v1/notifications/actions/b",
          elsewhere: "https://evil.test/x",
        },
        actions_taken: ["watch"],
      }),
    );
    expect(item.title).toBe("Cloud migration services");
    expect(item.detail).toBe("Department of Energy · High fit");
    expect(item.href).toBe("/app/opportunities/opp-1");
    expect(item.score).toBe(82.4);
    expect(item.actions.map((a) => a.action)).toEqual(["pursue", "pass"]);
    expect(item.actionsTaken).toEqual(["watch"]);
  });

  it("falls back to an event title and copes with an empty payload", () => {
    const item = toBellItem(row({}, { event_type: "deadline_reminder" }));
    expect(item.title).toBe("Deadline reminder");
    expect(item.detail).toBeNull();
    expect(item.href).toBeNull();
    expect(item.actions).toEqual([]);
    expect(item.score).toBeNull();
  });

  it("names an event type it has never seen", () => {
    expect(eventTitle("brand_new_thing", {})).toBe("brand new thing");
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-09-27T12:00:00Z");

  it("reads in minutes, hours and days", () => {
    expect(relativeTime("2026-09-27T11:59:40Z", now)).toBe("just now");
    expect(relativeTime("2026-09-27T11:30:00Z", now)).toBe("30m ago");
    expect(relativeTime("2026-09-27T09:00:00Z", now)).toBe("3h ago");
    expect(relativeTime("2026-09-25T12:00:00Z", now)).toBe("2d ago");
  });

  it("falls back to a date beyond a week and ignores rubbish", () => {
    expect(relativeTime("2026-09-01T12:00:00Z", now)).toMatch(/Sep/);
    expect(relativeTime("nonsense", now)).toBe("");
  });
});
