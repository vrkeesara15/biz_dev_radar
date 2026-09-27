import { describe, expect, it } from "vitest";

import {
  PAID_PLANS,
  PLANS,
  PLAN_COMPARISON,
  exhausted,
  gstErrors,
  isPaidPlan,
  isUpgrade,
  needsGst,
  planAction,
  planRank,
  resourceLabel,
  usageBar,
  usageBars,
} from "./plans";

describe("plan ranking", () => {
  it("orders free < pro < enterprise", () => {
    expect(planRank("free")).toBeLessThan(planRank("pro"));
    expect(planRank("pro")).toBeLessThan(planRank("enterprise"));
    expect(planRank("nonsense")).toBe(-1);
  });

  it("knows an upgrade from a downgrade", () => {
    expect(isUpgrade("free", "pro")).toBe(true);
    expect(isUpgrade("pro", "enterprise")).toBe(true);
    expect(isUpgrade("enterprise", "pro")).toBe(false);
    expect(isUpgrade("pro", "pro")).toBe(false);
  });

  it("labels the call to action per column", () => {
    expect(planAction("free", "pro")).toBe("upgrade");
    expect(planAction("pro", "pro")).toBe("current");
    expect(planAction("enterprise", "pro")).toBe("downgrade");
  });

  it("offers checkout for the paid plans only", () => {
    expect(PAID_PLANS).toEqual(["pro", "enterprise"]);
    expect(isPaidPlan("free")).toBe(false);
    expect(isPaidPlan("enterprise")).toBe(true);
  });
});

describe("the comparison table", () => {
  it("gives every plan a value in every row", () => {
    for (const feature of PLAN_COMPARISON) {
      for (const plan of PLANS) {
        expect(feature.values[plan], `${feature.label} / ${plan}`).toBeTruthy();
      }
    }
  });

  it("says what SPEC 3 says about the free plan", () => {
    const byLabel = Object.fromEntries(PLAN_COMPARISON.map((f) => [f.label, f.values]));
    expect(byLabel["Company profiles"].free).toBe("1");
    expect(byLabel["Source regions"].free).toBe("1");
    expect(byLabel["Alerts"].free).toBe("Digest only");
    expect(byLabel["Agent drafts per month"].pro).toBe("10");
    expect(byLabel["SSO"].enterprise).toBe("Yes");
  });
});

describe("usage bars", () => {
  it("reads a limited resource", () => {
    const bar = usageBar({ resource: "profiles", limit: 3, used: 1, remaining: 2 });
    expect(bar).toMatchObject({ label: "Company profiles", percent: 33, unlimited: false, atLimit: false });
    expect(bar.text).toBe("1 of 3");
  });

  it("reads an unlimited resource without pretending it is full", () => {
    const bar = usageBar({ resource: "profiles", limit: null, used: 12, remaining: null });
    expect(bar.unlimited).toBe(true);
    expect(bar.percent).toBe(0);
    expect(bar.atLimit).toBe(false);
    expect(bar.text).toBe("12 used · unlimited");
  });

  it("calls a zero limit what it is", () => {
    const bar = usageBar({ resource: "agent_drafts_per_month", limit: 0, used: 0, remaining: 0 });
    expect(bar.text).toBe("Not included on this plan");
    expect(bar.atLimit).toBe(true);
    expect(bar.percent).toBe(0);
  });

  it("caps the bar at 100 and flags being at the limit", () => {
    const bar = usageBar({ resource: "profiles", limit: 3, used: 5, remaining: 0 });
    expect(bar.percent).toBe(100);
    expect(bar.atLimit).toBe(true);
  });

  it("orders the rows as SPEC 3 lists them and appends the unknown", () => {
    const rows = [
      { resource: "surprise", limit: 1, used: 0, remaining: 1 },
      { resource: "agent_drafts_per_month", limit: 10, used: 4, remaining: 6 },
      { resource: "profiles", limit: 3, used: 3, remaining: 0 },
    ];
    expect(usageBars(rows).map((bar) => bar.resource)).toEqual([
      "profiles",
      "agent_drafts_per_month",
      "surprise",
    ]);
    expect(exhausted(rows).map((bar) => bar.resource)).toEqual(["profiles"]);
  });

  it("humanises an unknown resource key", () => {
    expect(resourceLabel("brand_new_limit")).toBe("brand new limit");
    expect(resourceLabel("source_regions")).toBe("Source regions");
  });
});

describe("GST", () => {
  it("is asked for by Razorpay only", () => {
    expect(needsGst("razorpay")).toBe(true);
    expect(needsGst("stripe")).toBe(false);
  });

  it("checks the GSTIN shape and the place of supply", () => {
    expect(gstErrors({})).toEqual({});
    expect(gstErrors({ gstin: "29ABCDE1234F1Z5", place_of_supply: "29" })).toEqual({});
    expect(gstErrors({ gstin: "too-short", place_of_supply: "29" }).gstin).toBeDefined();
    expect(gstErrors({ gstin: "29ABCDE1234F1Z5" }).place_of_supply).toMatch(/place of supply/i);
    expect(gstErrors({ place_of_supply: "Karnataka" }).place_of_supply).toMatch(/two-digit/i);
  });
});
