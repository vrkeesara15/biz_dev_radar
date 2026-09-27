import { describe, expect, it } from "vitest";

import { normalizeDashboard, stageLabel } from "./api";

describe("normalizeDashboard", () => {
  it("reads the M6 aggregate into the Home view model", () => {
    const dashboard = normalizeDashboard({
      open_by_stage: { submitted: 2, identified: 5, reviewing: 3 },
      due_next_7_days: [
        {
          id: "d1",
          title: "Questions due",
          due_at: "2026-10-01T18:00:00Z",
          kind: "questions_due",
          buyer: "GSA",
          pursuit_id: "p1",
        },
      ],
      pipeline_value_by_stage: {
        identified: { USD: 1000000, INR: 83000000 },
        submitted: { USD: 500000, INR: null },
      },
      win_rate: 0.32,
      avg_hours_saved_per_package: 11.5,
      alert_precision: 0.74,
    });

    expect(dashboard.openByStage).toEqual([
      { stage: "identified", count: 5 },
      { stage: "reviewing", count: 3 },
      { stage: "submitted", count: 2 },
    ]);
    expect(dashboard.dueNext7Days[0]).toMatchObject({ title: "Questions due", pursuit_id: "p1" });
    expect(dashboard.pipelineTotal).toEqual({ USD: 1_500_000, INR: 83_000_000 });
    expect(dashboard.winRate).toBe(0.32);
    expect(dashboard.avgHoursSavedPerPackage).toBe(11.5);
    expect(dashboard.alertPrecision).toBe(0.74);
  });

  it("accepts one total per currency instead of a stage breakdown", () => {
    const dashboard = normalizeDashboard({ pipeline_value_by_stage: { USD: "250000.50", INR: "20000000" } });
    expect(dashboard.pipelineValueByStage).toEqual([]);
    expect(dashboard.pipelineTotal).toEqual({ USD: 250000.5, INR: 20000000 });
  });

  it("sorts unknown stages after the known ones", () => {
    const dashboard = normalizeDashboard({ open_by_stage: { zeta: 1, won: 2, alpha: 3 } });
    expect(dashboard.openByStage.map((entry) => entry.stage)).toEqual(["won", "alpha", "zeta"]);
  });

  it("survives an empty or malformed body", () => {
    const empty = normalizeDashboard(null);
    expect(empty.openByStage).toEqual([]);
    expect(empty.dueNext7Days).toEqual([]);
    expect(empty.pipelineTotal).toEqual({ USD: null, INR: null });
    expect(empty.winRate).toBeNull();

    const messy = normalizeDashboard({ due_next_7_days: [null, "x", { title: "No id" }] });
    expect(messy.dueNext7Days).toHaveLength(1);
    expect(messy.dueNext7Days[0].id).toBe("due-0");
    expect(messy.dueNext7Days[0].due_at).toBeNull();
  });

  it("reads the alternative key spellings the aggregate may use", () => {
    const dashboard = normalizeDashboard({
      due_next_7_days: [{ opportunity_id: "o9", name: "Sources sought", response_due_at: "2026-10-03T00:00:00Z" }],
      pipeline_value_by_stage: { drafting: { usd: 42 } },
    });
    expect(dashboard.dueNext7Days[0]).toMatchObject({
      id: "o9",
      title: "Sources sought",
      due_at: "2026-10-03T00:00:00Z",
    });
    expect(dashboard.pipelineTotal.USD).toBe(42);
  });
});

describe("stageLabel", () => {
  it("humanises the stage keys", () => {
    expect(stageLabel("no_bid")).toBe("No bid");
    expect(stageLabel("won")).toBe("Won");
  });
});
