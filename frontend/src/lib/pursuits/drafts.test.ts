import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/opportunities/api";
import {
  asStaleVersion,
  flagRanges,
  flagTitle,
  needsInputChips,
  orphanNeedsInput,
  reloadVersion,
  STALE_FALLBACK,
} from "@/lib/pursuits/drafts";

describe("flagRanges (flags -> editor decorations)", () => {
  const text =
    "Acme has delivered 14 cloud migrations for federal agencies. " +
    "Our team holds an ISO 27001 certificate. " +
    "We will staff the work from Hyderabad.";

  it("finds each flagged sentence and leaves grounded text alone", () => {
    const ranges = flagRanges(text, [
      { sentence: "Our team holds an ISO 27001 certificate.", reason: "certification", detail: "no record" },
    ]);
    expect(ranges).toHaveLength(1);
    expect(text.slice(ranges[0].from, ranges[0].to)).toBe("Our team holds an ISO 27001 certificate.");
    expect(ranges[0].reason).toBe("certification");
  });

  it("returns ranges in document order whatever order the flags arrive in", () => {
    const ranges = flagRanges(text, [
      { sentence: "We will staff the work from Hyderabad.", reason: "place" },
      { sentence: "Acme has delivered 14 cloud migrations for federal agencies.", reason: "number" },
    ]);
    expect(ranges.map((range) => range.reason)).toEqual(["number", "place"]);
    expect(ranges[0].from).toBeLessThan(ranges[1].from);
  });

  it("matches across a line break, because body_text wraps differently", () => {
    const wrapped = "Acme has delivered\n14 cloud migrations for federal agencies.";
    const ranges = flagRanges(wrapped, [
      { sentence: "Acme has delivered 14 cloud migrations for federal agencies.", reason: "number" },
    ]);
    expect(ranges).toHaveLength(1);
    expect(ranges[0].from).toBe(0);
    expect(ranges[0].to).toBe(wrapped.length);
  });

  it("drops a flag whose sentence the writer has already deleted", () => {
    expect(flagRanges(text, [{ sentence: "We hold a Top Secret facility clearance.", reason: "clearance" }])).toEqual(
      [],
    );
  });

  it("never overlaps two decorations on the same words", () => {
    const repeated = "We are ISO certified. We are ISO certified.";
    const ranges = flagRanges(repeated, [
      { sentence: "We are ISO certified.", reason: "one" },
      { sentence: "We are ISO certified.", reason: "two" },
    ]);
    expect(ranges).toHaveLength(2);
    expect(ranges[0].to).toBeLessThanOrEqual(ranges[1].from);
  });

  it("is safe with regex metacharacters, empty input and missing flags", () => {
    expect(flagRanges("Cost is $1,000 (plus tax).", [{ sentence: "Cost is $1,000 (plus tax).", reason: "price" }])).toHaveLength(1);
    expect(flagRanges("", [{ sentence: "anything", reason: "r" }])).toEqual([]);
    expect(flagRanges(text, [])).toEqual([]);
    expect(flagRanges(text, null)).toEqual([]);
    expect(flagRanges(text, [{ sentence: "   ", reason: "blank" }])).toEqual([]);
  });

  it("titles a decoration with its reason and detail", () => {
    expect(flagTitle({ reason: "past_performance", detail: "no matching record" })).toBe(
      "Unsupported claim (past performance): no matching record",
    );
    expect(flagTitle({ reason: "number", detail: "" })).toBe("Unsupported claim (number)");
  });
});

describe("needsInputChips ([NEEDS INPUT: ...] parser)", () => {
  const body =
    "Our rate for [NEEDS INPUT: labor category] is [NEEDS INPUT: price]. " +
    "The site lead is [NEEDS INPUT].";

  it("reads every marker with its position and links the task behind it", () => {
    const chips = needsInputChips(body, [
      { placeholder: "[NEEDS INPUT: price]", question: "What is the loaded rate?", task_id: "t-2" },
      { placeholder: "[NEEDS INPUT: labor category]", question: "Which category?", task_id: "t-1" },
    ]);
    expect(chips.map((chip) => chip.label)).toEqual(["labor category", "price", ""]);
    expect(chips[0].taskId).toBe("t-1");
    expect(chips[1].question).toBe("What is the loaded rate?");
    expect(chips[2].taskId).toBeNull();
    expect(body.slice(chips[1].from, chips[1].to)).toBe("[NEEDS INPUT: price]");
  });

  it("is case- and whitespace-insensitive like the backend regex", () => {
    const chips = needsInputChips("a [ needs   input :  DUNS number ] b", [
      { placeholder: "[NEEDS INPUT: DUNS number]", task_id: "t-9" },
    ]);
    expect(chips).toHaveLength(1);
    expect(chips[0].label).toBe("DUNS number");
    expect(chips[0].taskId).toBe("t-9");
  });

  it("never spans a line, so a stray bracket cannot swallow a paragraph", () => {
    expect(needsInputChips("[NEEDS INPUT: one\nstill text]", [])).toEqual([]);
  });

  it("lists the stored asks no marker accounts for any more", () => {
    const items = [
      { placeholder: "[NEEDS INPUT: price]", task_id: "t-2" },
      { placeholder: "[NEEDS INPUT: bonding capacity]", task_id: "t-3" },
    ];
    expect(orphanNeedsInput(body, items).map((item) => item.task_id)).toEqual(["t-3"]);
  });

  it("handles an empty body and no items", () => {
    expect(needsInputChips("", [])).toEqual([]);
    expect(needsInputChips(body, null)).toHaveLength(3);
  });
});

describe("asStaleVersion (optimistic versioning conflict)", () => {
  const conflict = (detail: unknown) => new ApiError(409, { detail });

  it("reads both version numbers out of the server's sentence", () => {
    const stale = asStaleVersion(
      conflict("draft past-performance is at version 4, not 3; reload the section and reapply your edit"),
    );
    expect(stale).not.toBeNull();
    expect(stale?.currentVersion).toBe(4);
    expect(stale?.baseVersion).toBe(3);
    expect(stale?.message).toContain("reload the section");
  });

  it("still reports a conflict when the message is not the one we know", () => {
    const stale = asStaleVersion(conflict("someone else is editing"));
    expect(stale?.currentVersion).toBeNull();
    expect(stale?.message).toBe("someone else is editing");
  });

  it("falls back to our own sentence when the body carries none", () => {
    expect(asStaleVersion(new ApiError(409, {}))?.message).toBe(STALE_FALLBACK);
  });

  it("is not a conflict for any other failure", () => {
    expect(asStaleVersion(new ApiError(403, { detail: "role writer may not" }))).toBeNull();
    expect(asStaleVersion(new ApiError(422, { detail: [] }))).toBeNull();
    expect(asStaleVersion(new Error("network"))).toBeNull();
    expect(asStaleVersion(null)).toBeNull();
  });

  it("reloads to the server's version, or to what we hold when it is unknown", () => {
    expect(reloadVersion({ error: "stale_version", currentVersion: 7, baseVersion: 5, message: "" }, 5)).toBe(7);
    expect(reloadVersion({ error: "stale_version", currentVersion: null, baseVersion: null, message: "" }, 5)).toBe(5);
  });
});
