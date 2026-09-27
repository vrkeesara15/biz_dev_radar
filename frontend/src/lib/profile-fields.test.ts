import { describe, expect, it } from "vitest";

import {
  CERTIFICATION_KINDS,
  CODE_SCHEMES,
  PROFILE_FIELDS,
  REGISTRATION_KINDS,
  STEPS,
  fieldsForRegion,
  isFieldAllowed,
  isFieldRequired,
  isMaskedValue,
  optionsForRegion,
  stripRegionForeign,
} from "./profile-fields";

const keys = (region: "US" | "IN", step?: 1 | 2 | 3 | 4 | 5 | 6 | 7) =>
  fieldsForRegion(region, step).map((f) => f.key);

describe("profile field metadata", () => {
  it("defines seven steps", () => {
    expect(STEPS.map((s) => s.id)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("has unique keys and a valid step on every field", () => {
    const seen = new Set<string>();
    for (const f of PROFILE_FIELDS) {
      expect(seen.has(f.key), `duplicate ${f.key}`).toBe(false);
      seen.add(f.key);
      expect(f.step).toBeGreaterThanOrEqual(1);
      expect(f.step).toBeLessThanOrEqual(6);
    }
  });

  it("shows US-only registrations to US and hides them from IN", () => {
    const us = keys("US", 1);
    const india = keys("IN", 1);
    for (const k of ["uei", "cage_code", "sam_status", "sam_expires_on", "ein", "vehicles"]) {
      expect(us).toContain(k);
      expect(india).not.toContain(k);
    }
  });

  it("shows IN-only registrations to IN and hides them from US", () => {
    const us = keys("US", 1);
    const india = keys("IN", 1);
    for (const k of [
      "pan",
      "gstin",
      "tan",
      "cin_llpin",
      "udyam_number",
      "udyam_category",
      "dpiit_number",
      "gem_seller_id",
      "registrations",
      "local_supplier_class",
      "local_content_pct",
    ]) {
      expect(india).toContain(k);
      expect(us).not.toContain(k);
    }
  });

  it("keeps shared identity fields in both regions", () => {
    for (const k of ["legal_name", "addresses", "website", "phone", "bid_inbox_email", "legal_structure"]) {
      expect(keys("US", 1)).toContain(k);
      expect(keys("IN", 1)).toContain(k);
    }
  });

  it("gates 4.2 status fields by region", () => {
    expect(keys("US", 2)).toContain("certifications.socio_economic");
    expect(keys("US", 2)).toContain("size_status_by_naics");
    expect(keys("IN", 2)).not.toContain("certifications.socio_economic");
    expect(keys("IN", 2)).toContain("mse_ownership");
    expect(keys("IN", 2)).toContain("net_worth");
    expect(keys("US", 2)).not.toContain("mse_ownership");
  });

  it("gates code schemes: NAICS/PSC/ALN for US, GeM/India categories for IN", () => {
    expect(optionsForRegion(CODE_SCHEMES, "US").map((o) => o.value)).toEqual(["naics", "psc", "aln"]);
    expect(optionsForRegion(CODE_SCHEMES, "IN").map((o) => o.value)).toEqual(["gem", "india_category"]);
  });

  it("gates certification and registration kinds", () => {
    const usCerts = optionsForRegion(CERTIFICATION_KINDS, "US").map((o) => o.value);
    const inCerts = optionsForRegion(CERTIFICATION_KINDS, "IN").map((o) => o.value);
    expect(usCerts).toContain("8a");
    expect(usCerts).toContain("fedramp");
    expect(usCerts).not.toContain("stqc");
    expect(inCerts).toContain("stqc");
    expect(inCerts).toContain("iso_27001");
    expect(inCerts).not.toContain("hubzone");
    expect(optionsForRegion(REGISTRATION_KINDS, "US").map((o) => o.value)).toEqual(["sam"]);
    expect(optionsForRegion(REGISTRATION_KINDS, "IN").map((o) => o.value)).not.toContain("sam");
  });

  it("answers isFieldAllowed / isFieldRequired per region", () => {
    expect(isFieldAllowed("uei", "US")).toBe(true);
    expect(isFieldAllowed("uei", "IN")).toBe(false);
    expect(isFieldAllowed("pan", "IN")).toBe(true);
    expect(isFieldAllowed("pan", "US")).toBe(false);
    expect(isFieldAllowed("legal_name", "IN")).toBe(true);
    expect(isFieldAllowed("some_unknown_field", "IN")).toBe(true);
    expect(isFieldRequired("uei", "US")).toBe(true);
    expect(isFieldRequired("cage_code", "US")).toBe(false);
    expect(isFieldRequired("legal_name", "IN")).toBe(true);
  });

  it("strips region-foreign columns from a payload", () => {
    const usPayload = stripRegionForeign(
      { legal_name: "Acme", uei: "ABC123DEF456", pan: "ABCDE1234F", mse_ownership: "women" },
      "US",
    );
    expect(usPayload).toEqual({ legal_name: "Acme", uei: "ABC123DEF456" });
    const inPayload = stripRegionForeign(
      { legal_name: "Acme", uei: "ABC123DEF456", pan: "ABCDE1234F", cleared_personnel_count: 3 },
      "IN",
    );
    expect(inPayload).toEqual({ legal_name: "Acme", pan: "ABCDE1234F" });
  });

  it("recognises masked encrypted values", () => {
    expect(isMaskedValue("•••••1234")).toBe(true);
    expect(isMaskedValue("12-3456789")).toBe(false);
    expect(isMaskedValue(null)).toBe(false);
  });
});
