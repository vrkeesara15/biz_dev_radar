import { describe, expect, it } from "vitest";

import type { PursuitOut } from "@/lib/pursuits/api";
import {
  activityTimeline,
  costMeter,
  DEFAULT_TAB,
  isTabId,
  readScorecard,
  runStatusLabel,
  TABS,
  usd,
} from "@/lib/pursuits/workspace";

const pursuit = (overrides: Partial<PursuitOut> = {}): PursuitOut =>
  ({
    id: "p1",
    profile_id: "pr1",
    opportunity_id: "o1",
    stage: "drafting",
    owner_user_id: null,
    decision: null,
    decided_by: null,
    decided_at: null,
    decision_note: null,
    package_approved_by: null,
    package_approved_at: null,
    internal_due_at: null,
    watch: false,
    pass_reason: null,
    submitted_at: null,
    activity_at: "2026-09-20T10:00:00Z",
    matrix_recheck_required: false,
    created_by: "u1",
    created_at: "2026-09-01T09:00:00Z",
    updated_at: "2026-09-20T10:00:00Z",
    cost_so_far_usd: "3.50",
    cost_cap_usd: "15.00",
    budget_month_limit_usd: "200.00",
    budget_month_spent_usd: "40.00",
    budget_month_remaining_usd: "160.00",
    drafts: {},
    run: null,
    ...overrides,
  }) as PursuitOut;

describe("tabs", () => {
  it("is SPEC 10.4 screen 6, in order", () => {
    expect(TABS.map((tab) => tab.label)).toEqual([
      "Bid/no-bid",
      "Compliance matrix",
      "Drafts",
      "Pricing",
      "Checklist",
      "Tasks",
      "Activity",
    ]);
    expect(isTabId(DEFAULT_TAB)).toBe(true);
    expect(isTabId("nope")).toBe(false);
  });
});

describe("costMeter", () => {
  it("reads the decimal strings the API sends", () => {
    const meter = costMeter(pursuit());
    expect(meter.spent).toBe(3.5);
    expect(meter.cap).toBe(15);
    expect(meter.percent).toBe(23);
    expect(meter.over).toBe(false);
    expect(meter.monthRemaining).toBe(160);
  });

  it("clamps at the cap and flags being near it", () => {
    const meter = costMeter(pursuit({ cost_so_far_usd: "20.00" }));
    expect(meter.percent).toBe(100);
    expect(meter.over).toBe(true);
    expect(meter.near).toBe(true);
  });

  it("survives a zero cap and an absent monthly budget", () => {
    const meter = costMeter(
      pursuit({ cost_cap_usd: "0", cost_so_far_usd: "0", budget_month_remaining_usd: null }),
    );
    expect(meter.percent).toBe(0);
    expect(meter.monthRemaining).toBeNull();
  });

  it("formats money for the meter", () => {
    expect(usd(3.5)).toBe("$3.50");
  });
});

describe("readScorecard", () => {
  const output = {
    scorecard: {
      fit: 82,
      eligibility: 100,
      capacity: 60,
      competition: 45,
      value_fit: 70,
      win_probability: 35,
      incumbent_note: "Leidos holds the incumbent task order.",
      gaps: [{ gap: "No FedRAMP High past performance", suggested_fix: "Team with a FedRAMP prime" }],
      teaming_suggestions: [{ partner_or_capability: "FedRAMP prime", why: "Covers the gap" }],
      recommendation: "bid",
      reasons: ["Strong NAICS fit", "Deadline is reachable"],
    },
    weights: { fit: 30 },
    weighted_score: "68.5",
    suggested_recommendation: "bid",
    version: 2,
  };

  it("reads the stored ScorecardOutput", () => {
    const card = readScorecard(output);
    expect(card?.fit).toBe(82);
    expect(card?.recommendation).toBe("bid");
    expect(card?.gaps).toHaveLength(1);
    expect(card?.teaming_suggestions[0].partner_or_capability).toBe("FedRAMP prime");
    expect(card?.reasons).toHaveLength(2);
    expect(card?.weighted_score).toBe("68.5");
    expect(card?.version).toBe(2);
  });

  it("unwraps an artifact row and a list of them", () => {
    expect(readScorecard({ kind: "scorecard", version: 3, data: output })?.fit).toBe(82);
    expect(readScorecard({ items: [{ data: output }] })?.win_probability).toBe(35);
    expect(readScorecard([{ data: output }])?.capacity).toBe(60);
  });

  it("clamps a nonsense score and survives a payload that is not one", () => {
    expect(readScorecard({ scorecard: { ...output.scorecard, fit: 1000 } })?.fit).toBe(100);
    expect(readScorecard({ scorecard: { ...output.scorecard, fit: "bad" } })?.fit).toBe(0);
    expect(readScorecard(null)).toBeNull();
    expect(readScorecard({ detail: "Not Found" })).toBeNull();
    expect(readScorecard("nope")).toBeNull();
  });
});

describe("activityTimeline", () => {
  it("composes the timeline newest first from what the API exposes", () => {
    const events = activityTimeline({
      pursuit: pursuit({
        decision: "bid",
        decided_at: "2026-09-10T09:00:00Z",
        decided_by: "u2",
        decision_note: "Strong fit",
        package_approved_at: "2026-09-18T09:00:00Z",
        package_approved_by: "u3",
        run: {
          id: "r1",
          kind: "pipeline",
          step: "red_team",
          status: "paused",
          pause_reason: "gate2: the draft package must be reviewed and approved",
          paused_at: null,
          gate: "gate2",
          cost_usd: "3.50",
          tokens_in: 1,
          tokens_out: 1,
          started_at: "2026-09-15T09:00:00Z",
          finished_at: "2026-09-15T09:30:00Z",
          created_at: "2026-09-15T09:00:00Z",
        },
      }),
      drafts: [
        {
          id: "d1",
          section_id: "past-performance",
          title: "Past performance",
          volume: null,
          status: "in_review",
          version: 3,
          unsupported_claims: 2,
          needs_input: 1,
          citations: 4,
          comments: 0,
          updated_at: "2026-09-16T09:00:00Z",
        },
      ],
      tasks: [
        {
          id: "t1",
          pursuit_id: "p1",
          title: "Confirm the ISO certificate",
          detail: null,
          assignee_user_id: null,
          due_at: null,
          status: "open",
          source: "agent",
          ref: {},
          created_by: null,
          completed_at: null,
          completed_by: null,
          created_at: "2026-09-16T10:00:00Z",
          updated_at: "2026-09-16T10:00:00Z",
        },
      ],
      comments: [
        {
          id: "c1",
          pursuit_id: "p1",
          target_type: "draft_section",
          target_id: "d1",
          body: "Tighten the second paragraph",
          author_user_id: "u4",
          resolved_at: null,
          resolved_by: null,
          created_at: "2026-09-17T09:00:00Z",
          updated_at: "2026-09-17T09:00:00Z",
        },
      ],
      exports: [
        {
          id: "e1",
          pursuit_id: "p1",
          format: "docx",
          version: 1,
          file_name: "proposal.docx",
          content_type: "application/vnd",
          size_bytes: 10,
          renderer: "python-docx",
          final: false,
          created_by: "u2",
          created_at: "2026-09-19T09:00:00Z",
        },
      ],
    });

    expect(events[0].kind).toBe("export");
    expect(events.at(-1)?.title).toBe("Pursuit created");
    expect(events.map((event) => event.kind)).toEqual([
      "export",
      "approval",
      "comment",
      "task",
      "draft",
      "run",
      "decision",
      "pursuit",
    ]);
    const decision = events.find((event) => event.kind === "decision");
    expect(decision?.title).toBe("Gate 1: bid");
    expect(decision?.detail).toBe("Strong fit");
    expect(events.find((event) => event.kind === "run")?.detail).toContain("gate2");
  });

  it("is just the creation for a pursuit nothing has happened to", () => {
    const events = activityTimeline({ pursuit: pursuit() });
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("pursuit");
  });

  it("labels run statuses", () => {
    expect(runStatusLabel("needs_approval")).toBe("Needs budget approval");
    expect(runStatusLabel(null)).toBe("No run yet");
  });
});
