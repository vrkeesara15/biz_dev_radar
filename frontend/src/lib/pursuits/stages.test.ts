import { describe, expect, it } from "vitest";

import {
  CLOSED_SECTION_LABEL,
  LADDER_STAGES,
  STAGES,
  TERMINAL_STAGES,
  boardColumns,
  gateBadges,
  initials,
  isStage,
  isTerminalStage,
  ladderIndex,
  sortStages,
  stageLabel,
  stageOrder,
} from "./stages";

describe("stage vocabulary", () => {
  it("is SPEC 9's eleven stages, ladder first then the four outcomes", () => {
    expect(LADDER_STAGES).toEqual([
      "identified",
      "qualifying",
      "bid_decision",
      "drafting",
      "in_review",
      "final_approval",
      "submitted",
    ]);
    expect(TERMINAL_STAGES).toEqual(["awarded", "lost", "cancelled", "no_bid"]);
    expect(STAGES).toHaveLength(11);
    expect(new Set(STAGES).size).toBe(11);
  });

  it("labels every stage in SPEC 9's own words", () => {
    expect(STAGES.map(stageLabel)).toEqual([
      "Identified",
      "Qualifying",
      "Bid decision",
      "Drafting",
      "In review",
      "Final approval",
      "Submitted",
      "Awarded",
      "Lost",
      "Cancelled",
      "No-bid",
    ]);
  });

  it("title-cases a stage this build has never heard of instead of dropping it", () => {
    expect(isStage("archived")).toBe(false);
    expect(stageLabel("on_hold")).toBe("On hold");
    expect(stageLabel("")).toBe("Unknown");
  });

  it("knows which stages are terminal", () => {
    expect(LADDER_STAGES.every((stage) => !isTerminalStage(stage))).toBe(true);
    expect(TERMINAL_STAGES.every(isTerminalStage)).toBe(true);
    expect(ladderIndex("submitted")).toBe(6);
    expect(ladderIndex("awarded")).toBe(-1);
  });
});

describe("ordering", () => {
  it("puts the ladder before the outcomes and unknown stages last", () => {
    expect(sortStages(["lost", "identified", "drafting", "zzz", "awarded"])).toEqual([
      "identified",
      "drafting",
      "awarded",
      "lost",
      "zzz",
    ]);
  });

  it("sorts a shuffled full set back into SPEC 9 order", () => {
    const shuffled = [...STAGES].reverse();
    expect(sortStages(shuffled)).toEqual([...STAGES]);
  });

  it("orders two unknown stages alphabetically rather than by arrival", () => {
    expect(stageOrder("mystery")).toBe(STAGES.length);
    expect(sortStages(["zeta", "alpha"])).toEqual(["alpha", "zeta"]);
  });
});

describe("boardColumns", () => {
  const counts = { identified: 3, drafting: 2, awarded: 1, lost: 4, archived: 7 };

  it("gives the seven ladder stages a column each, even at zero", () => {
    const columns = boardColumns(counts);
    expect(columns.open.map((c) => c.stage)).toEqual([...LADDER_STAGES]);
    expect(columns.open.map((c) => c.count)).toEqual([3, 0, 0, 2, 0, 0, 0]);
    expect(columns.open.every((c) => !c.terminal)).toBe(true);
  });

  it("groups the four outcomes — and an unknown stage — under Closed", () => {
    const columns = boardColumns(counts);
    expect(CLOSED_SECTION_LABEL).toBe("Closed");
    expect(columns.closed.map((c) => c.stage)).toEqual([
      "awarded",
      "lost",
      "cancelled",
      "no_bid",
      "archived",
    ]);
    expect(columns.closedCount).toBe(1 + 4 + 0 + 0 + 7);
    expect(columns.closed.every((c) => c.terminal)).toBe(true);
  });

  it("survives a missing or malformed by_stage map", () => {
    expect(boardColumns(null).open).toHaveLength(7);
    expect(boardColumns(undefined).closedCount).toBe(0);
    expect(boardColumns({ identified: Number.NaN } as unknown as Record<string, number>).open[0].count).toBe(0);
  });
});

describe("gateBadges", () => {
  it("flags Gate 1 only while a card sits in Bid decision without a decision", () => {
    expect(gateBadges({ stage: "bid_decision", decision: null }).map((b) => b.label)).toEqual([
      "Needs bid decision",
    ]);
    expect(gateBadges({ stage: "bid_decision", decision: "bid" })).toEqual([]);
    expect(gateBadges({ stage: "qualifying", decision: null })).toEqual([]);
  });

  it("flags Gate 2 on a card awaiting final approval", () => {
    expect(gateBadges({ stage: "final_approval" }).map((b) => b.key)).toEqual(["package_approval"]);
  });

  it("flags an amendment-driven matrix re-check on any stage, alongside a gate", () => {
    expect(
      gateBadges({ stage: "final_approval", matrix_recheck_required: true }).map((b) => b.key),
    ).toEqual(["package_approval", "matrix_recheck"]);
    expect(gateBadges({ stage: "drafting", matrix_recheck_required: true })).toHaveLength(1);
  });
});

describe("initials", () => {
  it("takes the first and last word of a name", () => {
    expect(initials("E2E Owner")).toBe("EO");
    expect(initials("Ada Byron Lovelace")).toBe("AL");
  });

  it("falls back to the email local part, then to a question mark", () => {
    expect(initials(null, "priya.sharma@example.com")).toBe("PS");
    expect(initials(null, "ops@example.com")).toBe("OP");
    expect(initials(null, null)).toBe("?");
  });
});
