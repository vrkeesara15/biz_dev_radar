import { describe, expect, it } from "vitest";

import {
  DEFAULT_FILTERS,
  activeFilterCount,
  describeFilters,
  filtersToRecord,
  parseFilters,
  parseNaicsList,
  sameSearch,
  searchHref,
  serializeFilters,
  splitList,
  toApiQuery,
  type OpportunityFilters,
} from "./filters";

const full: OpportunityFilters = {
  q: "cloud migration",
  region: "us",
  type: ["rfp", "rfq"],
  naics: ["541511", "541512"],
  due_before: "2026-10-15",
  status: ["open", "closing_soon"],
  min_score: 70,
  page: 3,
  page_size: 50,
};

describe("parseFilters", () => {
  it("returns defaults for an empty query", () => {
    expect(parseFilters("")).toEqual(DEFAULT_FILTERS);
    expect(parseFilters(null)).toEqual(DEFAULT_FILTERS);
    expect(parseFilters(new URLSearchParams())).toEqual(DEFAULT_FILTERS);
  });

  it("reads every key from a query string, with or without the leading ?", () => {
    const query =
      "?q=cloud+migration&region=us&type=rfp,rfq&naics=541511,541512&due_before=2026-10-15&status=open,closing_soon&min_score=70&page=3&page_size=50";
    expect(parseFilters(query)).toEqual(full);
    expect(parseFilters(query.slice(1))).toEqual(full);
  });

  it("drops unknown enum values, bad dates and out-of-range numbers instead of failing", () => {
    const parsed = parseFilters(
      "region=eu&type=rfp,bogus,RFQ&status=open,zzz&naics=541511,abc,12&due_before=2026-13-40&min_score=250&page=0&page_size=1000",
    );
    expect(parsed.region).toBeNull();
    expect(parsed.type).toEqual(["rfp", "rfq"]);
    expect(parsed.status).toEqual(["open"]);
    expect(parsed.naics).toEqual(["541511", "12"]);
    expect(parsed.due_before).toBeNull();
    expect(parsed.min_score).toBe(100);
    expect(parsed.page).toBe(1);
    expect(parsed.page_size).toBe(100);
  });

  it("accepts a saved-search record (plain object) with the same keys", () => {
    expect(parseFilters({ q: "roads", region: "in", type: "gem_bid", min_score: "50" })).toEqual({
      ...DEFAULT_FILTERS,
      q: "roads",
      region: "in",
      type: ["gem_bid"],
      min_score: 50,
    });
  });

  it("clamps a negative score to zero and keeps zero as an explicit filter", () => {
    expect(parseFilters("min_score=-5").min_score).toBe(0);
    expect(parseFilters("min_score=0").min_score).toBe(0);
    expect(parseFilters("min_score=").min_score).toBeNull();
  });
});

describe("serializeFilters", () => {
  it("omits defaults and writes keys in a stable order", () => {
    expect(serializeFilters(DEFAULT_FILTERS)).toBe("");
    expect(serializeFilters(full)).toBe(
      "q=cloud+migration&region=us&type=rfp%2Crfq&naics=541511%2C541512&due_before=2026-10-15&status=open%2Cclosing_soon&min_score=70&page=3&page_size=50",
    );
  });

  it("round-trips through parseFilters", () => {
    expect(parseFilters(serializeFilters(full))).toEqual(full);
    const partial = { ...DEFAULT_FILTERS, q: "a & b = c", min_score: 0 };
    expect(parseFilters(serializeFilters(partial))).toEqual(partial);
  });

  it("drops page 1 and the default page size so a saved URL stays canonical", () => {
    expect(serializeFilters({ ...DEFAULT_FILTERS, page: 1, page_size: 25, q: "x" })).toBe("q=x");
    expect(serializeFilters({ ...DEFAULT_FILTERS, page: 2 })).toBe("page=2");
  });

  it("builds a search href and the home high-fit link", () => {
    expect(searchHref({})).toBe("/app/opportunities");
    expect(searchHref({ min_score: 70 })).toBe("/app/opportunities?min_score=70");
  });
});

describe("filtersToRecord / sameSearch / activeFilterCount", () => {
  it("stores the search without paging for a saved search", () => {
    expect(filtersToRecord(full)).toEqual({
      q: "cloud migration",
      region: "us",
      type: "rfp,rfq",
      naics: "541511,541512",
      due_before: "2026-10-15",
      status: "open,closing_soon",
      min_score: "70",
    });
    expect(filtersToRecord(DEFAULT_FILTERS)).toEqual({});
  });

  it("ignores paging when comparing searches", () => {
    expect(sameSearch(full, { ...full, page: 1, page_size: 25 })).toBe(true);
    expect(sameSearch(full, { ...full, q: "other" })).toBe(false);
  });

  it("counts the controls in use", () => {
    expect(activeFilterCount(DEFAULT_FILTERS)).toBe(0);
    expect(activeFilterCount(full)).toBe(7);
  });
});

describe("toApiQuery", () => {
  it("maps to the API's comma lists and end-of-day due_before", () => {
    expect(toApiQuery(full)).toEqual({
      page: 3,
      page_size: 50,
      q: "cloud migration",
      region: "us",
      type: "rfp,rfq",
      naics: "541511,541512",
      due_before: "2026-10-15T23:59:59Z",
      status: "open,closing_soon",
      min_score: 70,
    });
  });

  it("sends only paging for the default search", () => {
    expect(toApiQuery(DEFAULT_FILTERS)).toEqual({ page: 1, page_size: 25 });
  });
});

describe("list helpers and descriptions", () => {
  it("splits on commas, spaces and semicolons without duplicates", () => {
    expect(splitList(" 541511, 541512;541511\n541519 ")).toEqual(["541511", "541512", "541519"]);
    expect(splitList(undefined)).toEqual([]);
  });

  it("keeps only numeric NAICS codes", () => {
    expect(parseNaicsList("541511 5415 x 1234567")).toEqual(["541511", "5415"]);
  });

  it("describes the filter set for a chip", () => {
    expect(describeFilters(DEFAULT_FILTERS)).toBe("All opportunities");
    expect(describeFilters(full)).toBe(
      "“cloud migration” · United States · RFP/RFQ · NAICS 541511, 541512 · Open/Closing soon · due by 2026-10-15 · score ≥ 70",
    );
  });
});
