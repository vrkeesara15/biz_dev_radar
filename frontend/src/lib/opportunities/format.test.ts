import { describe, expect, it } from "vitest";

import { buyerPath, changeKindLabel, formatBytes, formatDiffValue, formatValueRange, truncate } from "./format";

describe("formatValueRange", () => {
  it("shows INR in lakh/crore with the USD normalisation alongside", () => {
    expect(
      formatValueRange({
        currency: "INR",
        estimated_value_min: "12000000",
        estimated_value_max: "20000000",
        estimated_value_min_usd: "144000",
        estimated_value_max_usd: "240000",
      }),
    ).toEqual({ primary: "₹1.20 Cr – ₹2.00 Cr", usd: "$144.0K – $240.0K" });
  });

  it("does not repeat USD for a USD record", () => {
    expect(
      formatValueRange({
        currency: "USD",
        estimated_value_min: "250000",
        estimated_value_max: "250000",
        estimated_value_min_usd: "250000",
        estimated_value_max_usd: "250000",
      }),
    ).toEqual({ primary: "$250.0K", usd: null });
  });

  it("handles open-ended and missing ranges", () => {
    const base = { currency: "USD", estimated_value_min_usd: null, estimated_value_max_usd: null };
    expect(formatValueRange({ ...base, estimated_value_min: "1000000", estimated_value_max: null }).primary).toBe("from $1.00M");
    expect(formatValueRange({ ...base, estimated_value_min: null, estimated_value_max: "5000" }).primary).toBe("up to $5.0K");
    expect(formatValueRange({ ...base, estimated_value_min: null, estimated_value_max: null })).toEqual({ primary: null, usd: null });
  });
});

describe("buyerPath / formatBytes / diff helpers", () => {
  it("prefers the hierarchy array and falls back to the three columns", () => {
    expect(buyerPath({ buyer_org: "GSA", buyer_sub_org: "FAS", buyer_office: null, buyer_hierarchy: ["GSA", "FAS", "ITC"] })).toEqual([
      "GSA",
      "FAS",
      "ITC",
    ]);
    expect(buyerPath({ buyer_org: "GSA", buyer_sub_org: " ", buyer_office: "ITC" })).toEqual(["GSA", "ITC"]);
  });

  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(20480)).toBe("20 KB");
    expect(formatBytes(2.5 * 1024 * 1024)).toBe("2.5 MB");
    expect(formatBytes(null)).toBeNull();
  });

  it("renders diff values readably", () => {
    expect(formatDiffValue(null)).toBe("—");
    expect(formatDiffValue("2026-10-14T18:00:00Z")).toBe("2026-10-14T18:00:00Z");
    expect(formatDiffValue(["541511", "541512"])).toBe("541511, 541512");
    expect(formatDiffValue([{ url: "https://x/a.pdf", file_name: "a.pdf" }, { url: "https://x/b.pdf" }])).toBe("a.pdf, https://x/b.pdf");
    expect(formatDiffValue({ a: 1 })).toBe('{"a":1}');
  });

  it("labels change kinds and truncates on a word boundary", () => {
    expect(changeKindLabel("deadline_moved")).toBe("Deadline moved");
    expect(changeKindLabel("brand_new_kind")).toBe("brand new kind");
    expect(truncate("The quick brown fox jumps over the lazy dog", 20)).toBe("The quick brown fox…");
    expect(truncate("short", 20)).toBe("short");
  });
});
