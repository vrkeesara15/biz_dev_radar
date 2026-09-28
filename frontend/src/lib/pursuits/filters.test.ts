import { describe, expect, it } from "vitest";

import {
  DEFAULT_PIPELINE_FILTERS,
  UNASSIGNED,
  activePipelineFilterCount,
  describePipelineFilters,
  needsClientOwnerFilter,
  parsePipelineFilters,
  pipelineHref,
  samePipelineSearch,
  serializePipelineFilters,
  toPursuitQuery,
  type PipelineFilters,
} from "./filters";

const OWNER = "8b1d0c8e-0000-4000-8000-000000000001";

const filters = (patch: Partial<PipelineFilters> = {}): PipelineFilters => ({
  ...DEFAULT_PIPELINE_FILTERS,
  ...patch,
});

describe("parsePipelineFilters", () => {
  it("defaults to the board with nothing filtered", () => {
    expect(parsePipelineFilters("")).toEqual(DEFAULT_PIPELINE_FILTERS);
    expect(parsePipelineFilters(null)).toEqual(DEFAULT_PIPELINE_FILTERS);
  });

  it("reads every SPEC 9 filter out of the query string", () => {
    const parsed = parsePipelineFilters(
      `view=table&owner=${OWNER}&due_before=2026-10-14&min_value=250000&region=in&stage=drafting,in_review&watch=true&page=3&page_size=50`,
    );
    expect(parsed).toEqual({
      view: "table",
      owner: OWNER,
      due_before: "2026-10-14",
      min_value: 250000,
      region: "in",
      stage: ["drafting", "in_review"],
      watch: true,
      page: 3,
      page_size: 50,
    });
  });

  it("drops values it does not recognise rather than failing a stale link", () => {
    const parsed = parsePipelineFilters(
      "view=gantt&region=eu&stage=drafting,teleporting&due_before=2026-13-01&min_value=-5&watch=maybe&page=0&page_size=9999",
    );
    expect(parsed.view).toBe("board");
    expect(parsed.region).toBeNull();
    expect(parsed.stage).toEqual(["drafting"]);
    expect(parsed.due_before).toBeNull();
    expect(parsed.min_value).toBeNull();
    expect(parsed.watch).toBeNull();
    expect(parsed.page).toBe(1);
    expect(parsed.page_size).toBe(100); // clamped to MAX_PAGE_SIZE
  });

  it("accepts a plain record as well as a query string", () => {
    expect(parsePipelineFilters({ stage: ["drafting", "submitted"], watch: "false" }).stage).toEqual([
      "drafting",
      "submitted",
    ]);
    expect(parsePipelineFilters({ watch: "false" }).watch).toBe(false);
  });
});

describe("serializePipelineFilters", () => {
  it("writes nothing for the default board", () => {
    expect(serializePipelineFilters(DEFAULT_PIPELINE_FILTERS)).toBe("");
    expect(pipelineHref({})).toBe("/app/pipeline");
  });

  it("writes a stable key order with defaults omitted", () => {
    const query = serializePipelineFilters(
      filters({ view: "table", owner: OWNER, region: "us", stage: ["drafting"], min_value: 1000, watch: false }),
    );
    expect(query).toBe(
      `view=table&owner=${OWNER}&min_value=1000&region=us&stage=drafting&watch=false`,
    );
  });

  it("carries pagination only in the table view", () => {
    expect(serializePipelineFilters(filters({ view: "board", page: 4 }))).toBe("");
    expect(serializePipelineFilters(filters({ view: "table", page: 4, page_size: 50 }))).toBe(
      "view=table&page=4&page_size=50",
    );
  });

  it("round-trips: parse(serialize(x)) === x", () => {
    for (const sample of [
      filters({ view: "table", owner: UNASSIGNED, page: 2 }),
      filters({ due_before: "2027-02-28", min_value: 0, region: "in" }),
      filters({ stage: ["identified", "no_bid"], watch: true }),
      filters({ view: "table", page_size: 100, page: 7, region: "us" }),
    ]) {
      expect(parsePipelineFilters(serializePipelineFilters(sample))).toEqual(sample);
    }
  });

  it("counts the filters in use, ignoring the view and the page", () => {
    expect(activePipelineFilterCount(DEFAULT_PIPELINE_FILTERS)).toBe(0);
    expect(activePipelineFilterCount(filters({ view: "table", page: 5 }))).toBe(0);
    expect(activePipelineFilterCount(filters({ owner: OWNER, region: "us", watch: false }))).toBe(3);
    // min_value 0 is a real filter ("anything with a value"), not an absent one.
    expect(activePipelineFilterCount(filters({ min_value: 0 }))).toBe(1);
  });

  it("treats a view or page change as the same search", () => {
    expect(samePipelineSearch(filters({ view: "board" }), filters({ view: "table", page: 3 }))).toBe(true);
    expect(samePipelineSearch(filters({ region: "us" }), filters({ region: "in" }))).toBe(false);
  });
});

describe("toPursuitQuery", () => {
  it("sends the end of the chosen day so a deadline on that date is included", () => {
    expect(toPursuitQuery(filters({ due_before: "2026-10-14" })).due_before).toBe("2026-10-14T23:59:59Z");
  });

  it("maps every filter onto the GET /pursuits parameters", () => {
    expect(
      toPursuitQuery(filters({ owner: OWNER, min_value: 250000, region: "in", stage: ["drafting", "in_review"], watch: true, page: 2, page_size: 50 })),
    ).toEqual({
      page: 2,
      page_size: 50,
      owner: OWNER,
      min_value: 250000,
      region: "in",
      stage: "drafting,in_review",
      watch: true,
    });
  });

  it("keeps `unassigned` out of the request and applies it on the client", () => {
    const query = toPursuitQuery(filters({ owner: UNASSIGNED }));
    expect(query.owner).toBeUndefined();
    expect(needsClientOwnerFilter(filters({ owner: UNASSIGNED }))).toBe(true);
    expect(needsClientOwnerFilter(filters({ owner: OWNER }))).toBe(false);
  });

  it("lets the caller override the page for the board's full sweep", () => {
    expect(toPursuitQuery(filters({ page: 1 }), { page: 3, pageSize: 200 })).toMatchObject({
      page: 3,
      page_size: 200,
    });
  });
});

describe("describePipelineFilters", () => {
  it("summarises the filters in words", () => {
    expect(describePipelineFilters(DEFAULT_PIPELINE_FILTERS)).toBe("All pursuits");
    expect(describePipelineFilters(filters({ owner: UNASSIGNED, watch: true }))).toBe(
      "Unassigned · watched",
    );
    expect(describePipelineFilters(filters({ owner: OWNER }), "Ada Lovelace")).toBe("Owner Ada Lovelace");
  });
});
