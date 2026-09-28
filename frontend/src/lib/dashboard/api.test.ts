import { describe, expect, it } from "vitest";

import {
  ALERT_PRECISION_EMPTY,
  WIN_RATE_EMPTY,
  formatRate,
  normalizeDashboard,
  stageLabel,
} from "./api";

/** A response shaped exactly like M6-08's DashboardOut. */
const body = {
  generated_at: "2026-09-27T09:00:00Z",
  open_by_stage: { drafting: 2, identified: 5, qualifying: 3 },
  due_next_7_days: [
    {
      pursuit_id: "p1",
      opportunity_id: "o1",
      title: "Cloud migration and managed services",
      stage: "drafting",
      owner_user_id: null,
      due_at: {
        utc: "2026-10-01T18:00:00Z",
        buyer_tz: "America/New_York",
        buyer_local: "2026-10-01T14:00:00-04:00",
        buyer_display: "Oct 1, 2:00 PM EDT",
        user_tz: "Asia/Kolkata",
        user_local: "2026-10-01T23:30:00+05:30",
        user_display: "11:30 PM IST",
        display: "Oct 1, 2:00 PM EDT = 11:30 PM IST",
      },
      countdown: "4d 9h",
    },
  ],
  pipeline_value_by_stage: {
    identified: { USD: "2500000.00", INR: "207500000.00" },
    drafting: { USD: "1500000.00", INR: "124500000.00" },
  },
  pipeline_value_total: { USD: "4000000.00", INR: "332000000.00" },
  win_rate: 0.32,
  awarded: 8,
  lost: 17,
  submitted: 25,
  avg_hours_saved_per_package: 20,
  hours_saved_total: 500,
  hours_saved_basis: "25 packages submitted × 20 hours saved per package (an estimate, not a measurement)",
  alert_precision: 0.74,
  alert_feedback_rated: 50,
};

describe("normalizeDashboard", () => {
  it("reads M6-08's aggregate into the Home view model", () => {
    const dashboard = normalizeDashboard(body);
    expect(dashboard.generatedAt).toBe("2026-09-27T09:00:00Z");
    expect(dashboard.dueNext7Days[0]).toMatchObject({ pursuit_id: "p1", countdown: "4d 9h" });
    expect(dashboard.pipelineTotal).toEqual({ usd: 4_000_000, inr: 332_000_000 });
    expect(dashboard.winRate).toBe(0.32);
    expect(dashboard.awarded).toBe(8);
    expect(dashboard.lost).toBe(17);
    expect(dashboard.submitted).toBe(25);
    expect(dashboard.avgHoursSavedPerPackage).toBe(20);
    expect(dashboard.hoursSavedTotal).toBe(500);
    expect(dashboard.hoursSavedBasis).toContain("an estimate");
    expect(dashboard.alertPrecision).toBe(0.74);
    expect(dashboard.alertFeedbackRated).toBe(50);
  });

  it("orders both stage maps by SPEC 9's board order, not by key", () => {
    const dashboard = normalizeDashboard(body);
    expect(dashboard.openByStage).toEqual([
      { stage: "identified", label: "Identified", count: 5 },
      { stage: "qualifying", label: "Qualifying", count: 3 },
      { stage: "drafting", label: "Drafting", count: 2 },
    ]);
    expect(dashboard.openTotal).toBe(10);
    expect(dashboard.pipelineValueByStage.map((row) => row.stage)).toEqual(["identified", "drafting"]);
    expect(dashboard.pipelineValueByStage[0]).toEqual({
      stage: "identified",
      label: "Identified",
      usd: 2_500_000,
      inr: 207_500_000,
    });
  });

  it("sorts a stage this build does not know after the eleven it does", () => {
    const dashboard = normalizeDashboard({ open_by_stage: { zeta: 1, submitted: 2, alpha: 3 } });
    expect(dashboard.openByStage.map((entry) => entry.stage)).toEqual(["submitted", "alpha", "zeta"]);
  });

  it("keeps an unknown rate null rather than reading it as zero", () => {
    const dashboard = normalizeDashboard({ ...body, win_rate: null, alert_precision: null });
    expect(dashboard.winRate).toBeNull();
    expect(dashboard.alertPrecision).toBeNull();
    expect(formatRate(dashboard.winRate, WIN_RATE_EMPTY)).toBe(WIN_RATE_EMPTY);
    expect(formatRate(dashboard.alertPrecision, ALERT_PRECISION_EMPTY)).toBe(ALERT_PRECISION_EMPTY);
    expect(formatRate(0, WIN_RATE_EMPTY)).toBe("0%");
    expect(formatRate(0.744, WIN_RATE_EMPTY)).toBe("74%");
  });

  it("survives an empty or malformed body", () => {
    const empty = normalizeDashboard(null);
    expect(empty.openByStage).toEqual([]);
    expect(empty.dueNext7Days).toEqual([]);
    expect(empty.pipelineTotal).toEqual({ usd: null, inr: null });
    expect(empty.winRate).toBeNull();
    expect(empty.hoursSavedTotal).toBe(0);
    expect(empty.hoursSavedBasis).toBe("an estimate per submitted package");
    expect(normalizeDashboard({ due_next_7_days: "nope" }).dueNext7Days).toEqual([]);
  });

  it("accepts lower-case currency keys as well as the API's upper-case ones", () => {
    const dashboard = normalizeDashboard({ pipeline_value_total: { usd: 42, inr: 3500 } });
    expect(dashboard.pipelineTotal).toEqual({ usd: 42, inr: 3500 });
  });
});

describe("stageLabel", () => {
  it("uses SPEC 9's own wording", () => {
    expect(stageLabel("no_bid")).toBe("No-bid");
    expect(stageLabel("bid_decision")).toBe("Bid decision");
    expect(stageLabel("archived")).toBe("Archived");
  });
});
