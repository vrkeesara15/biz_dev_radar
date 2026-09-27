import { describe, expect, it } from "vitest";

import { completenessBadges, describeMissing, stepProgress, topMissing } from "./completeness";

const base = { missing: [], sections: {} };

describe("completenessBadges", () => {
  it("turns both badges off on an empty profile", () => {
    const badges = completenessBadges({ ...base, score: 3, matching_enabled: false, drafting_enabled: false }, 0);
    expect(badges.map((b) => [b.id, b.tone])).toEqual([
      ["matching", "off"],
      ["drafting", "off"],
    ]);
    expect(badges[0].detail).toContain("40");
    expect(badges[1].detail).toContain("70");
    expect(badges[1].detail).toContain("3 more past performance");
  });

  it("turns matching on at 40 and keeps drafting off", () => {
    const badges = completenessBadges({ ...base, score: 44, matching_enabled: true, drafting_enabled: false }, 1);
    expect(badges[0]).toMatchObject({ label: "Matching on", tone: "on" });
    expect(badges[1]).toMatchObject({ label: "Drafting off", tone: "off" });
    expect(badges[1].detail).toContain("2 more past performance");
  });

  it("explains drafting is blocked only by past performance when score is high", () => {
    const badges = completenessBadges({ ...base, score: 75, matching_enabled: true, drafting_enabled: false }, 2);
    expect(badges[1].detail).toBe("To draft: add 1 more past performance.");
    const unknownCount = completenessBadges({ ...base, score: 75, matching_enabled: true, drafting_enabled: false });
    expect(unknownCount[1].detail).toContain("at least 3 past performances");
  });

  it("turns drafting on when the API says so", () => {
    const badges = completenessBadges({ ...base, score: 82, matching_enabled: true, drafting_enabled: true }, 3);
    expect(badges[1]).toMatchObject({ label: "Drafting on", tone: "on" });
  });
});

describe("missing items", () => {
  it("maps section.item keys to labels and steps", () => {
    const items = describeMissing(["registrations.uei", "proof.past_performance", "preferences.notification_prefs"]);
    expect(items).toEqual([
      { key: "registrations.uei", label: "UEI", step: 1, section: "registrations" },
      { key: "proof.past_performance", label: "Past performance", step: 5, section: "proof" },
      { key: "preferences.notification_prefs", label: "Notification preferences", step: 6, section: "preferences" },
    ]);
  });

  it("limits to the top N and humanises unknown keys", () => {
    const items = topMissing(["identity.legal_name", "size_finance.some_new_item", "proof.boilerplate", "proof.rate_card"], 3);
    expect(items).toHaveLength(3);
    expect(items[1].label).toBe("some new item");
    expect(items[1].step).toBe(2);
  });
});

describe("stepProgress", () => {
  it("runs from 0 on step 1 to 100 on step 7", () => {
    expect(stepProgress(1)).toBe(0);
    expect(stepProgress(4)).toBe(50);
    expect(stepProgress(7)).toBe(100);
  });
});
