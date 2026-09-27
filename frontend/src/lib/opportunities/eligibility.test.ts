import { describe, expect, it } from "vitest";

import { eligibilityChecks, humanizeKey } from "./eligibility";

describe("eligibilityChecks", () => {
  it("renders an evaluated result set with pass/fail/unknown and citations", () => {
    const out = eligibilityChecks({
      status: "fail",
      score: "0.5",
      blocking: ["gem_registration"],
      exemptions: ["mse"],
      results: [
        { name: "turnover", status: "pass", reason: "avg ₹2.4 Cr vs ₹2 Cr minimum", page: 12 },
        { name: "experience", status: "unknown", reason: "founding year missing" },
        { name: "gem_registration", status: "fail", reason: "no GeM seller ID", citation: "Section 4.2" },
      ],
    });
    expect(out.overall).toBe("fail");
    expect(out.score).toBe(0.5);
    expect(out.blocking).toEqual(["gem_registration"]);
    expect(out.exemptions).toEqual(["mse"]);
    expect(out.checks.map((c) => [c.label, c.status, c.citation])).toEqual([
      ["Turnover", "pass", "p. 12"],
      ["Experience", "unknown", null],
      ["GeM registration", "fail", "Section 4.2"],
    ]);
    expect(out.checks[0].detail).toBe("avg ₹2.4 Cr vs ₹2 Cr minimum");
  });

  it("lists extracted criteria as unknown checks with their values", () => {
    const out = eligibilityChecks({
      min_avg_turnover_inr: "20000000",
      turnover_years: 3,
      required_certifications: ["ISO 9001", "ISO 27001"],
      requires_dsc: true,
      allows_mse_exemption: false,
      due_on: "2026-10-15",
    });
    expect(out.overall).toBeNull();
    expect(out.checks.every((c) => c.status === "unknown")).toBe(true);
    expect(out.checks.map((c) => [c.label, c.detail])).toEqual([
      ["Minimum average turnover", "20000000"],
      ["Turnover years averaged", "3"],
      ["Required certifications", "ISO 9001, ISO 27001"],
      ["Digital signature certificate", "Yes"],
      ["MSE exemption", "No"],
      ["Bid due date", "2026-10-15"],
    ]);
  });

  it("handles the Grants.gov shape and unknown keys", () => {
    const out = eligibilityChecks({
      applicant_types: ["State governments", "Nonprofits"],
      cost_sharing: false,
      something_new: { threshold: 5, unit: "years" },
    });
    expect(out.checks.map((c) => c.label)).toEqual(["Eligible applicant types", "Cost sharing required", "Something new"]);
    expect(out.checks[2].detail).toBe("Threshold: 5; Unit: years");
  });

  it("returns no checks for empty or non-object input", () => {
    expect(eligibilityChecks({}).checks).toEqual([]);
    expect(eligibilityChecks(null).checks).toEqual([]);
    expect(eligibilityChecks("text").checks).toEqual([]);
  });

  it("marks blocking criteria as failed when the extractor left them unknown", () => {
    const out = eligibilityChecks({ blocking: ["dsc"], results: [{ name: "dsc", reason: "expired" }] });
    expect(out.checks[0].status).toBe("fail");
  });

  it("humanises keys", () => {
    expect(humanizeKey("past_performance_years")).toBe("Past performance years");
    expect(humanizeKey("requires_dsc")).toBe("Digital signature certificate");
  });
});
