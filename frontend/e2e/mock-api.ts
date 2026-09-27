/**
 * In-memory stand-in for the backend, wired through `page.route("**\/api/v1/**")`.
 * It mirrors the routes the wizard uses (profiles, 13 sub-resources, files,
 * notification prefs, autofill) and recomputes a simplified completeness
 * score on every read so the meter visibly moves as steps are saved. It also
 * serves the M4 bell, push-subscription, action-link and preference routes,
 * the opportunities search/detail routes (M2-15) from fixtures with the
 * same filter semantics as the API, the platform-admin console routes (M7-08:
 * sources, run history, tenants, usage, health, support access) with in-memory
 * state so "Run now" and a plan edit really change what the next read returns,
 * and answers 404 for the pipeline actions (M6-01) and saved searches (M4-08)
 * that do not exist yet, so the screens' "not available yet" paths are
 * exercised for real.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Page, Route } from "@playwright/test";

type Json = Record<string, unknown>;

/** Shape of e2e/fixtures/admin.json (the M7-08 console routes). */
type AdminFixture = {
  sources: Json[];
  runs: Record<string, Json[]>;
  tenants: Json[];
  planLimits: Record<string, Record<string, number | null>>;
  usage: { current: Json[]; previous: Json[] };
  health: Json;
};

/** "YYYY-MM" of today in UTC, the period the console asks for by default. */
export const currentPeriod = (date = new Date()) =>
  `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;

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
  /** When true, GET/POST /saved-searches work in memory (M4-08 contract); default 404. */
  savedSearches?: boolean;
  /** When true, GET/POST/PATCH /alert-rules work in memory (M4-08 contract); default 404. */
  alertRules?: boolean;
  /** When true, POST /opportunities/{id}/feedback answers 201 (M4-08 contract); default 404. */
  feedback?: boolean;
  /** When true, GET /dashboard serves e2e/fixtures/dashboard.json (M6 contract); default 404. */
  dashboard?: boolean;
  /**
   * When true the opportunity fixtures carry a match, and `min_score` filters on
   * it the way the scored API will. Off by default so the M2 specs keep seeing
   * "Not scored yet".
   */
  matches?: boolean;
  /** When true, POST /opportunities/{id}/pursue|watch|pass answer 201 (M6-01 contract); default 404. */
  pipelineActions?: boolean;
};

/** Scores attached to the three opportunity fixtures when `matches` is on. */
const FIXTURE_SCORES: Record<string, number> = {
  "7c1d2e3f-0000-4000-8000-000000000001": 82,
  "7c1d2e3f-0000-4000-8000-000000000002": 74,
  "7c1d2e3f-0000-4000-8000-000000000003": 41,
};

export class MockApi {
  profile: Json | null = null;
  prefs: Json = fixture("notification-prefs.json");
  prefsSaved = false;
  collections = Object.fromEntries(RESOURCES.map((r) => [r, [] as Json[]])) as unknown as Record<Resource, Json[]>;
  requests: { method: string; path: string; search: string; body: unknown }[] = [];
  opportunities: Json[] = fixture<Json[]>("opportunities.json");
  opportunityDetail: Json = fixture<Json>("opportunity-detail.json");
  savedSearches: Json[] = [];
  alertRules: Json[] = [];
  notifications: Json[] = fixture<Json[]>("notifications.json");
  pushSubscriptions: Json[] = [];
  feedback: Json[] = [];
  dashboard: Json = fixture<Json>("dashboard.json");
  actionsTaken: { action: string; token: string; reason: string | null }[] = [];
  admin: AdminFixture = fixture<AdminFixture>("admin.json");
  adminSources: Json[] = this.admin.sources;
  adminTenants: Json[] = this.admin.tenants;
  adminGrants: Json[] = [];
  private seq = 0;

  constructor(private readonly options: MockOptions = {}) {
    if (options.matches) {
      this.opportunities = this.opportunities.map((row) => ({
        ...row,
        match: {
          score: FIXTURE_SCORES[String(row.id)] ?? null,
          band: (FIXTURE_SCORES[String(row.id)] ?? 0) >= 70 ? "high" : "low",
          breakdown: [],
        },
      }));
    }
  }

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
    this.requests.push({ method, path: pathname, search: url.search, body });
    const json = (status: number, payload: unknown) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });

    if (pathname.startsWith("/api/v1/admin/")) {
      const handled = this.handleAdmin(pathname, method, body, url.searchParams, json);
      if (handled) return handled;
    }
    if (pathname === "/api/v1/opportunities" && method === "GET") {
      return json(200, this.searchOpportunities(url.searchParams));
    }
    const actionMatch = pathname.match(/^\/api\/v1\/opportunities\/([^/]+)\/(pursue|watch|pass)$/);
    if (actionMatch && method === "POST") {
      if (!this.options.pipelineActions) return json(404, { detail: "Not Found" });
      const [, opportunityId, action] = actionMatch;
      return json(201, { id: this.nextId("pursuit"), opportunity_id: opportunityId, action, ...(body as Json) });
    }
    const opportunityMatch = pathname.match(/^\/api\/v1\/opportunities\/([^/]+)$/);
    if (opportunityMatch && method === "GET") {
      const [, opportunityId] = opportunityMatch;
      if (this.opportunityDetail.id === opportunityId) return json(200, this.opportunityDetail);
      return json(404, { detail: "opportunity not found" });
    }
    const notificationsHandled = this.handleNotifications(pathname, method, body, url.searchParams, json, route);
    if (notificationsHandled) return notificationsHandled;
    if (pathname === "/api/v1/dashboard" && method === "GET") {
      if (!this.options.dashboard) return json(404, { detail: "Not Found" });
      return json(200, this.dashboard);
    }
    const feedbackMatch = pathname.match(/^\/api\/v1\/opportunities\/([^/]+)\/feedback$/);
    if (feedbackMatch && method === "POST") {
      if (!this.options.feedback) return json(404, { detail: "Not Found" });
      const [, opportunityId] = feedbackMatch;
      const item = { id: this.nextId("feedback"), opportunity_id: opportunityId, ...(body as Json) };
      this.feedback.push(item);
      return json(201, item);
    }
    if (pathname === "/api/v1/alert-rules") {
      if (!this.options.alertRules) return json(404, { detail: "Not Found" });
      if (method === "GET") return json(200, this.alertRules);
      if (method === "POST") {
        const item = {
          id: this.nextId("rule"),
          saved_search_id: null,
          profile_id: null,
          min_score: 70,
          channels: ["email"],
          mode: "digest",
          enabled: true,
          ...(body as Json),
        };
        this.alertRules.push(item);
        return json(201, item);
      }
    }
    const ruleMatch = pathname.match(/^\/api\/v1\/alert-rules\/([^/]+)$/);
    if (ruleMatch && method === "PATCH") {
      if (!this.options.alertRules) return json(404, { detail: "Not Found" });
      const [, ruleId] = ruleMatch;
      const index = this.alertRules.findIndex((rule) => rule.id === ruleId);
      if (index === -1) return json(404, { detail: "alert rule not found" });
      this.alertRules[index] = { ...this.alertRules[index], ...(body as Json) };
      return json(200, this.alertRules[index]);
    }
    const savedMatch = pathname.match(/^\/api\/v1\/saved-searches\/([^/]+)$/);
    if (savedMatch) {
      if (!this.options.savedSearches) return json(404, { detail: "Not Found" });
      const [, savedId] = savedMatch;
      const index = this.savedSearches.findIndex((item) => item.id === savedId);
      if (index === -1) return json(404, { detail: "saved search not found" });
      if (method === "PATCH") {
        this.savedSearches[index] = { ...this.savedSearches[index], ...(body as Json) };
        return json(200, this.savedSearches[index]);
      }
      if (method === "DELETE") {
        this.savedSearches.splice(index, 1);
        this.alertRules = this.alertRules.filter((rule) => rule.saved_search_id !== savedId);
        return route.fulfill({ status: 204, body: "" });
      }
    }
    if (pathname === "/api/v1/saved-searches") {
      if (!this.options.savedSearches) return json(404, { detail: "Not Found" });
      if (method === "GET") return json(200, this.savedSearches);
      if (method === "POST") {
        const item = { id: this.nextId("saved"), created_at: new Date().toISOString(), ...(body as Json) };
        this.savedSearches.push(item);
        return json(201, item);
      }
    }
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

  /** The M7-08 admin console routes, platform_admin only on the real backend. */
  private handleAdmin(
    pathname: string,
    method: string,
    body: unknown,
    params: URLSearchParams,
    json: (status: number, payload: unknown) => Promise<void>,
  ): Promise<void> | null {
    const page = (items: Json[], extra: Json = {}) => {
      const pageNumber = Math.max(1, Number(params.get("page") ?? 1));
      const pageSize = Math.max(1, Number(params.get("page_size") ?? 25));
      const start = (pageNumber - 1) * pageSize;
      return {
        items: items.slice(start, start + pageSize),
        total: items.length,
        page: pageNumber,
        page_size: pageSize,
        pages: Math.max(1, Math.ceil(items.length / pageSize)),
        ...extra,
      };
    };

    if (pathname === "/api/v1/admin/sources" && method === "GET") {
      return json(200, this.adminSources);
    }
    if (pathname === "/api/v1/admin/health" && method === "GET") {
      return json(200, this.admin.health);
    }
    if (pathname === "/api/v1/admin/usage" && method === "GET") {
      const period = params.get("period") ?? currentPeriod();
      const items = period === currentPeriod() ? this.admin.usage.current : this.admin.usage.previous;
      const sum = (key: string) => items.reduce((acc, row) => acc + Number(row[key] ?? 0), 0);
      const cost = sum("cost_microusd");
      return json(200, {
        period,
        items,
        total_tokens_in: sum("tokens_in"),
        total_tokens_out: sum("tokens_out"),
        total_cost_microusd: cost,
        total_cost_usd: cost / 1_000_000,
        total_agent_runs: sum("agent_runs"),
      });
    }
    const runsMatch = pathname.match(/^\/api\/v1\/admin\/sources\/([^/]+)\/runs$/);
    if (runsMatch && method === "GET") {
      const [, sourceId] = runsMatch;
      if (!this.adminSources.some((s) => s.source_id === sourceId)) {
        return json(404, { detail: "unknown source" });
      }
      return json(200, page(this.admin.runs[sourceId] ?? [], { source_id: sourceId }));
    }
    const runMatch = pathname.match(/^\/api\/v1\/admin\/sources\/([^/]+)\/run$/);
    if (runMatch && method === "POST") {
      const [, sourceId] = runMatch;
      const source = this.adminSources.find((s) => s.source_id === sourceId);
      if (!source) return json(404, { detail: "unknown source" });
      if (!source.registered) return json(409, { detail: `source '${sourceId}' is disabled (stub)` });
      const startedAt = new Date().toISOString();
      source.last_run_at = startedAt;
      source.last_status = "ok";
      source.health_status = "ok";
      source.consecutive_failures = 0;
      const run = {
        id: this.nextId("run"),
        source_id: sourceId,
        started_at: startedAt,
        finished_at: startedAt,
        status: "ok",
        fetched: 12,
        upserted: 3,
        error_count: 0,
        last_error: null,
      };
      this.admin.runs[sourceId] = [run, ...(this.admin.runs[sourceId] ?? [])];
      return json(200, { source_id: sourceId, mode: "queued", task_id: this.nextId("task"), result: null });
    }
    if (pathname === "/api/v1/admin/tenants" && method === "GET") {
      const q = (params.get("q") ?? "").trim().toLowerCase();
      const rows = this.adminTenants.filter(
        (t) =>
          !q ||
          String(t.name).toLowerCase().includes(q) ||
          String(t.slug).toLowerCase().includes(q),
      );
      return json(200, page(rows));
    }
    const tenantMatch = pathname.match(/^\/api\/v1\/admin\/tenants\/([^/]+)(?:\/([^/]+))?$/);
    if (tenantMatch) {
      const [, tenantId, sub] = tenantMatch;
      const tenant = this.adminTenants.find((t) => t.id === tenantId);
      if (!tenant) return json(404, { detail: "tenant not found" });
      if (!sub && method === "GET") {
        const period = params.get("period") ?? currentPeriod();
        const usageRows = period === currentPeriod() ? this.admin.usage.current : this.admin.usage.previous;
        const usage = usageRows.find((row) => row.tenant_id === tenantId) ?? {
          tenant_id: tenantId,
          slug: tenant.slug,
          name: tenant.name,
          plan: tenant.plan,
          region: tenant.region,
          tokens_in: 0,
          tokens_out: 0,
          cost_microusd: 0,
          cost_usd: 0,
          agent_runs: 0,
          notifications: null,
        };
        const grant = this.adminGrants.filter((g) => g.tenant_id === tenantId).at(-1) ?? null;
        return json(200, {
          tenant,
          plan_limits: this.admin.planLimits[String(tenant.plan)] ?? {},
          period,
          usage,
          billing:
            tenant.slug === "alpha-corp"
              ? { provider: "stripe", status: "active", plan: tenant.plan, current_period_end: null, has_subscription: true }
              : null,
          support_access: grant,
        });
      }
      if (!sub && method === "PATCH") {
        const patch = (body ?? {}) as Json;
        if (patch.plan !== undefined && patch.plan !== null) tenant.plan = patch.plan;
        if (patch.is_internal !== undefined && patch.is_internal !== null) tenant.is_internal = patch.is_internal;
        return json(200, tenant);
      }
      if (sub === "support-access" && method === "POST") {
        const payload = (body ?? {}) as Json;
        const reason = String(payload.reason ?? "");
        if (reason.trim().length < 3) return json(422, { detail: "reason is required" });
        const minutes = Number(payload.minutes ?? 60);
        const grantedAt = new Date();
        const grant = {
          id: this.nextId("grant"),
          tenant_id: tenantId,
          admin_user_id: "00000000-0000-0000-0000-0000000000e2",
          reason,
          granted_at: grantedAt.toISOString(),
          expires_at: new Date(grantedAt.getTime() + minutes * 60_000).toISOString(),
        };
        this.adminGrants.push(grant);
        return json(200, {
          tenant,
          member_count: Number(tenant.member_count ?? 0),
          reason,
          grant,
        });
      }
      if (sub === "audit-log" && method === "GET") {
        const grant = this.adminGrants.filter((g) => g.tenant_id === tenantId).at(-1);
        if (!grant) {
          return json(403, {
            detail: "support access to this tenant has not been granted (or has expired)",
          });
        }
        return json(200, { ...page([]), grant });
      }
    }
    return null;
  }

  /** The M4 bell, push-subscription and one-click action routes. */
  private handleNotifications(
    pathname: string,
    method: string,
    body: unknown,
    params: URLSearchParams,
    json: (status: number, payload: unknown) => Promise<void>,
    route: Route,
  ): Promise<void> | null {
    if (pathname === "/api/v1/me/notifications" && method === "GET") {
      const unreadOnly = ["1", "true"].includes((params.get("unread") ?? "").toLowerCase());
      const limit = Math.max(1, Number(params.get("limit") ?? 50));
      const rows = this.notifications
        .filter((row) => !unreadOnly || row.read_at === null)
        .sort((a, b) => Date.parse(String(b.created_at)) - Date.parse(String(a.created_at)));
      return json(200, {
        items: rows.slice(0, limit),
        unread: this.notifications.filter((row) => row.read_at === null).length,
      });
    }
    if (pathname === "/api/v1/me/notifications/read-all" && method === "POST") {
      const readAt = new Date().toISOString();
      let marked = 0;
      for (const row of this.notifications) {
        if (row.read_at === null) {
          row.read_at = readAt;
          marked += 1;
        }
      }
      return json(200, { marked });
    }
    const readMatch = pathname.match(/^\/api\/v1\/me\/notifications\/([^/]+)\/read$/);
    if (readMatch && method === "POST") {
      const [, notificationId] = readMatch;
      const row = this.notifications.find((item) => item.id === notificationId);
      if (!row) return json(404, { detail: "notification not found" });
      row.read_at = row.read_at ?? new Date().toISOString();
      return json(200, { id: row.id, read_at: row.read_at });
    }
    if (pathname === "/api/v1/me/push-subscriptions") {
      if (method === "POST") {
        const item = { id: this.nextId("push"), created: true, ...(body as Json) };
        this.pushSubscriptions.push(item);
        return json(201, item);
      }
      if (method === "DELETE") {
        const endpoint = String((body as Json)?.endpoint ?? "");
        const before = this.pushSubscriptions.length;
        this.pushSubscriptions = this.pushSubscriptions.filter((item) => item.endpoint !== endpoint);
        if (this.pushSubscriptions.length === before) return json(404, { detail: "subscription not found" });
        return route.fulfill({ status: 204, body: "" });
      }
    }
    const actionMatch = pathname.match(/^\/api\/v1\/notifications\/actions\/([^/]+)$/);
    if (actionMatch && method === "GET") {
      const [, token] = actionMatch;
      const action = token.replace(/^tok-/, "");
      this.actionsTaken.push({ action, token, reason: params.get("reason") });
      return json(202, {
        action,
        notification_id: this.notifications[0]?.id ?? null,
        opportunity_id: null,
        pursuit_id: null,
        recorded: true,
        redirect: "https://app.bidradar.test/app",
      });
    }
    return null;
  }

  /** GET /opportunities with the API's filter semantics over the fixture rows. */
  private searchOpportunities(params: URLSearchParams): Json {
    const csv = (key: string) => (params.get(key) ?? "").split(",").map((v) => v.trim()).filter(Boolean);
    const q = (params.get("q") ?? "").trim().toLowerCase();
    const region = params.get("region");
    const types = csv("type");
    const statuses = csv("status");
    const naics = csv("naics");
    const dueBefore = params.get("due_before") ? Date.parse(params.get("due_before")!) : null;
    // Only meaningful once the rows carry a match; the unscored fixtures ignore it.
    const minScore = this.options.matches && params.get("min_score") ? Number(params.get("min_score")) : null;
    const rows = this.opportunities.filter((row) => {
      const text = `${row.title ?? ""} ${row.summary_ai ?? ""}`.toLowerCase();
      if (q && !q.split(/\s+/).every((term) => text.includes(term))) return false;
      if (region && row.region !== region) return false;
      if (types.length && !types.includes(String(row.notice_type))) return false;
      if (statuses.length && !statuses.includes(String(row.status))) return false;
      if (naics.length && !(row.naics as string[]).some((code) => naics.includes(code))) return false;
      if (minScore !== null) {
        const score = Number((row.match as Json | null)?.score ?? NaN);
        if (!Number.isFinite(score) || score < minScore) return false;
      }
      if (dueBefore !== null) {
        const due = row.response_due_at ? Date.parse(String(row.response_due_at)) : null;
        if (due === null || due > dueBefore) return false;
      }
      return true;
    });
    const page = Math.max(1, Number(params.get("page") ?? 1));
    const pageSize = Math.max(1, Number(params.get("page_size") ?? 25));
    const start = (page - 1) * pageSize;
    return {
      items: rows.slice(start, start + pageSize),
      total: rows.length,
      page,
      page_size: pageSize,
      pages: Math.max(1, Math.ceil(rows.length / pageSize)),
    };
  }

  private regionForeign(region: string, body: Json): string[] {
    const us = ["uei", "cage_code", "sam_status", "sam_expires_on", "ein", "cleared_personnel_count"];
    const india = ["pan", "gstin", "tan", "cin_llpin", "udyam_number", "udyam_category", "dpiit_number", "gem_seller_id", "local_supplier_class", "local_content_pct", "net_worth_amount", "net_worth_currency", "solvency_certificate_available", "mse_ownership"];
    const foreign = region === "in" ? us : india;
    return Object.keys(body).filter((k) => foreign.includes(k) && body[k] !== null && body[k] !== undefined);
  }
}
