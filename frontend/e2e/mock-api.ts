/**
 * In-memory stand-in for the backend, wired through `page.route("**\/api/v1/**")`.
 * It mirrors the routes the wizard uses (profiles, 13 sub-resources, files,
 * notification prefs, autofill) and recomputes a simplified completeness
 * score on every read so the meter visibly moves as steps are saved.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Page, Route } from "@playwright/test";

type Json = Record<string, unknown>;

const fixture = <T>(name: string): T =>
  JSON.parse(readFileSync(path.join(__dirname, "fixtures", name), "utf8")) as T;

export const RESOURCES = [
  "codes",
  "keywords",
  "service-lines",
  "certifications",
  "teaming-partners",
  "past-performance",
  "personnel",
  "registrations",
  "vehicles",
  "insurance",
  "boilerplate",
  "files",
  "rate-card",
] as const;
type Resource = (typeof RESOURCES)[number];

/** A fresh copy of the empty profile fixture. */
export const baseProfile = (): Json => fixture<Json>("profile.json");

export type MockOptions = {
  /** When false, POST /autofill answers 404 like a server without M1-10. */
  autofill?: boolean;
  region?: "us" | "in";
};

export class MockApi {
  profile: Json | null = null;
  prefs: Json = fixture("notification-prefs.json");
  prefsSaved = false;
  collections = Object.fromEntries(RESOURCES.map((r) => [r, [] as Json[]])) as unknown as Record<Resource, Json[]>;
  requests: { method: string; path: string; body: unknown }[] = [];
  private seq = 0;

  constructor(private readonly options: MockOptions = {}) {}

  private nextId(prefix: string) {
    this.seq += 1;
    return `${prefix}-${String(this.seq).padStart(4, "0")}`;
  }

  /** Current score as the API would report it (what the meter must show). */
  score(): string {
    return String((this.completeness() as { score: number }).score);
  }

  completeness(): Json {
    const p = this.profile;
    if (!p) return { score: 0, matching_enabled: false, drafting_enabled: false, missing: [], sections: {} };
    const c = this.collections;
    const has = (k: string) => {
      const v = p[k];
      return Array.isArray(v) ? v.length > 0 : v !== null && v !== undefined && v !== "";
    };
    const region = p.region as string;
    const items: [string, string, number, boolean][] = [
      ["identity", "legal_name", 3, has("legal_name")],
      ["identity", "address", 3, has("addresses")],
      ["identity", "website", 2, has("website")],
      ["identity", "phone", 2, has("phone")],
      ["identity", "bid_inbox_email", 2, has("bid_inbox_email")],
      ["identity", "year_founded", 1, has("year_founded")],
      ["identity", "legal_structure", 2, has("legal_structure")],
      ...(region === "us"
        ? ([
            ["registrations", "uei", 4, has("uei")],
            ["registrations", "sam_status", 2, has("sam_status")],
            ["registrations", "sam_expires_on", 2, has("sam_expires_on")],
            ["registrations", "cage_code", 2, has("cage_code")],
          ] as [string, string, number, boolean][])
        : ([
            ["registrations", "pan", 3, has("pan")],
            ["registrations", "gstin", 3, has("gstin")],
            ["registrations", "cin_llpin", 1, has("cin_llpin")],
            ["registrations", "udyam_or_dpiit", 1, has("udyam_number") || has("dpiit_number")],
            ["registrations", "gem_seller_id", 2, has("gem_seller_id")],
          ] as [string, string, number, boolean][])),
      ["size_finance", "employee_count_total", 5, has("employee_count_total")],
      ["size_finance", "annual_revenue", 5, has("annual_revenue")],
      ["size_finance", "bonding_capacity", 5, has("bonding_capacity_amount")],
      ["what_we_sell", "codes", 6, c.codes.length > 0],
      ["what_we_sell", "keywords", 4, c.keywords.length > 0],
      ["what_we_sell", "service_lines", 6, c["service-lines"].length > 0],
      ["what_we_sell", "capability_statement", 4, c.files.some((f) => f.kind === "capability_statement")],
      ["where_how_big", "target_geography", 3, has("target_countries") || has("target_us_states") || has("target_in_states") || has("target_cities")],
      ["where_how_big", "value_range", 3, has("value_min_usd") || has("value_max_usd") || has("value_min_inr") || has("value_max_inr")],
      ["where_how_big", "notice_types_wanted", 2, has("notice_types_wanted")],
      ["where_how_big", "buyers", 2, has("target_buyers") || has("blocked_buyers")],
      ["proof", "past_performance", 8, c["past-performance"].length >= 3],
      ["proof", "personnel", 4, c.personnel.length > 0],
      ["proof", "certifications_or_insurance", 4, c.certifications.length > 0 || c.insurance.length > 0],
      ["proof", "boilerplate", 4, c.boilerplate.length > 0],
      ["preferences", "scoring_weights_reviewed", 3, this.prefsSaved],
      ["preferences", "required_approver_roles", 2, has("required_approver_roles")],
      ["preferences", "output_languages", 2, has("output_languages")],
      ["preferences", "notification_prefs", 3, this.prefsSaved],
    ];
    const sections: Record<string, { score: number; weight: number; missing: string[] }> = {};
    const missing: string[] = [];
    let score = 0;
    for (const [section, name, points, ok] of items) {
      const s = (sections[section] ??= { score: 0, weight: 0, missing: [] });
      s.weight += points;
      if (ok) {
        s.score += points;
        score += points;
      } else {
        s.missing.push(`${section}.${name}`);
        missing.push(`${section}.${name}`);
      }
    }
    // Partial credit for 1-2 past performances so the meter moves before drafting unlocks.
    const pp = c["past-performance"].length;
    if (pp > 0 && pp < 3) {
      const partial = pp === 1 ? 3 : 5;
      score += partial;
      sections.proof.score += partial;
    }
    return {
      score,
      matching_enabled: score >= 40,
      drafting_enabled: score >= 70 && pp >= 3,
      missing,
      sections,
    };
  }

  private profileOut(): Json {
    return { ...this.profile, completeness: this.completeness() };
  }

  async install(page: Page) {
    await page.route("**/api/v1/**", (route) => this.handle(route));
  }

  private async handle(route: Route) {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const pathname = url.pathname;
    let body: unknown = null;
    const raw = request.postData();
    if (raw && (request.headers()["content-type"] ?? "").includes("application/json")) {
      try {
        body = JSON.parse(raw);
      } catch {
        body = raw;
      }
    }
    this.requests.push({ method, path: pathname, body });
    const json = (status: number, payload: unknown) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });

    if (pathname === "/api/v1/files" && method === "POST") {
      return json(201, { ...fixture<Json>("file.json"), id: this.nextId("file") });
    }
    if (pathname === "/api/v1/me/notification-prefs") {
      if (method === "PUT") {
        this.prefs = { ...this.prefs, ...(body as Json) };
        this.prefsSaved = true;
      }
      return json(200, this.prefs);
    }
    if (pathname === "/api/v1/profiles") {
      if (method === "GET") return json(200, this.profile ? [this.profileOut()] : []);
      if (method === "POST") {
        const b = body as Json;
        // Region gate mirrors the backend: foreign fields -> 422 region_mismatch.
        const foreign = this.regionForeign(b.region as string, b);
        if (foreign.length) return json(422, { detail: { error: "region_mismatch", region: b.region, fields: foreign } });
        this.profile = { ...fixture<Json>("profile.json"), ...b, region: b.region ?? this.options.region ?? "us" };
        return json(201, this.profileOut());
      }
    }
    const profileMatch = pathname.match(/^\/api\/v1\/profiles\/([^/]+)(?:\/([^/]+))?(?:\/([^/]+))?$/);
    if (profileMatch) {
      const [, profileId, resource, itemId] = profileMatch;
      if (!this.profile || this.profile.id !== profileId) return json(404, { detail: "Profile not found" });
      if (!resource) {
        if (method === "GET") return json(200, this.profileOut());
        if (method === "PUT") {
          const b = body as Json;
          const foreign = this.regionForeign(this.profile.region as string, b);
          if (foreign.length) {
            return json(422, { detail: { error: "region_mismatch", region: this.profile.region, fields: foreign } });
          }
          this.profile = { ...this.profile, ...b, version: Number(this.profile.version) + 1 };
          return json(200, this.profileOut());
        }
      }
      if (resource === "autofill" && method === "POST") {
        if (this.options.autofill === false) return json(404, { detail: "Not Found" });
        return json(200, fixture("autofill.json"));
      }
      if ((RESOURCES as readonly string[]).includes(resource)) {
        const list = this.collections[resource as Resource];
        if (!itemId) {
          if (method === "GET") return json(200, list);
          if (method === "POST") {
            const item = { id: this.nextId(resource), profile_id: profileId, created_at: new Date().toISOString(), ...(body as Json) };
            list.push(item);
            return json(201, item);
          }
        } else {
          const index = list.findIndex((i) => i.id === itemId);
          if (index === -1) return json(404, { detail: "Not found" });
          if (method === "GET") return json(200, list[index]);
          if (method === "PUT") {
            list[index] = { ...list[index], ...(body as Json) };
            return json(200, list[index]);
          }
          if (method === "DELETE") {
            list.splice(index, 1);
            return route.fulfill({ status: 204, body: "" });
          }
        }
      }
    }
    return json(404, { detail: `Unhandled ${method} ${pathname}` });
  }

  private regionForeign(region: string, body: Json): string[] {
    const us = ["uei", "cage_code", "sam_status", "sam_expires_on", "ein", "cleared_personnel_count"];
    const india = ["pan", "gstin", "tan", "cin_llpin", "udyam_number", "udyam_category", "dpiit_number", "gem_seller_id", "local_supplier_class", "local_content_pct", "net_worth_amount", "net_worth_currency", "solvency_certificate_available", "mse_ownership"];
    const foreign = region === "in" ? us : india;
    return Object.keys(body).filter((k) => foreign.includes(k) && body[k] !== null && body[k] !== undefined);
  }
}
