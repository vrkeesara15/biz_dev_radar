import { afterEach, describe, expect, it, vi } from "vitest";

import type { Profile } from "@/lib/api/browser";

import {
  applySuggestion,
  groupSuggestionsByStep,
  highConfidenceIndexes,
  isSuggestionAllowed,
  parseSuggestionField,
  requestAutofill,
  suggestionStep,
  type AutofillSuggestion,
} from "./autofill";

const suggestion = (field: string, value: unknown, confidence = 0.9): AutofillSuggestion => ({
  field,
  value,
  source: "website",
  source_ref: "https://acme.example/about",
  confidence,
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseSuggestionField", () => {
  it("splits root, sub-path and list marker", () => {
    expect(parseSuggestionField("legal_name")).toEqual({ root: "legal_name", sub: null, isList: false });
    expect(parseSuggestionField("addresses[]")).toEqual({ root: "addresses", sub: null, isList: true });
    expect(parseSuggestionField("codes.naics[]")).toEqual({ root: "codes", sub: "naics", isList: true });
  });
});

describe("grouping", () => {
  it("groups suggestions by target step in step order", () => {
    const groups = groupSuggestionsByStep([
      suggestion("past_performance[]", { title: "Help desk" }),
      suggestion("legal_name", "Acme"),
      suggestion("codes.naics[]", "541511"),
      suggestion("certifications[]", { kind: "8a" }),
      suggestion("certifications[]", { kind: "iso_27001" }),
    ]);
    expect([...groups.keys()]).toEqual([1, 2, 3, 5]);
    expect(groups.get(1)?.map((s) => s.index)).toEqual([1]);
    expect(groups.get(2)?.map((s) => s.field)).toEqual(["certifications[]"]);
    expect(groups.get(5)?.map((s) => s.index)).toEqual([0, 4]);
  });

  it("maps fields to steps and picks high-confidence indexes", () => {
    expect(suggestionStep(suggestion("uei", "X"))).toBe(1);
    expect(suggestionStep(suggestion("service_lines[]", {}))).toBe(3);
    expect(suggestionStep(suggestion("target_us_states[]", ["VA"]))).toBe(4);
    const list = [suggestion("a", 1, 0.95), suggestion("b", 1, 0.8), suggestion("c", 1, 0.79)];
    expect(highConfidenceIndexes(list)).toEqual([0, 1]);
  });

  it("hides region-foreign suggestions", () => {
    expect(isSuggestionAllowed(suggestion("uei", "X"), "US")).toBe(true);
    expect(isSuggestionAllowed(suggestion("uei", "X"), "IN")).toBe(false);
    expect(isSuggestionAllowed(suggestion("codes.naics[]", "541511"), "IN")).toBe(false);
    expect(isSuggestionAllowed(suggestion("codes.gem[]", "IT services"), "IN")).toBe(true);
    expect(isSuggestionAllowed(suggestion("certifications[]", { kind: "8a" }), "IN")).toBe(false);
    expect(isSuggestionAllowed(suggestion("certifications[]", { kind: "iso_9001" }), "IN")).toBe(true);
  });
});

describe("requestAutofill", () => {
  it("reports the endpoint as unavailable on 404", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ detail: "Not Found" }, 404));
    const result = await requestAutofill("p1", { website_url: "https://acme.example" }, fetcher);
    expect(result).toEqual({ status: "unavailable" });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/profiles/p1/autofill",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("returns suggestions and warnings on success", async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse({ suggestions: [suggestion("legal_name", "Acme")], warnings: ["uei skipped"] }),
    );
    const result = await requestAutofill("p1", { uei: "ABC123DEF456" }, fetcher);
    expect(result.status).toBe("ok");
    if (result.status === "ok") {
      expect(result.data.suggestions).toHaveLength(1);
      expect(result.data.warnings).toEqual(["uei skipped"]);
    }
  });

  it("surfaces other errors with a message", async () => {
    const fetcher = vi.fn(async () => jsonResponse({ detail: "website unreachable" }, 502));
    const result = await requestAutofill("p1", { website_url: "https://x" }, fetcher);
    expect(result).toEqual({ status: "error", message: "website unreachable" });
  });
});

describe("applySuggestion", () => {
  const profile = { id: "p1", region: "us", addresses: [], dba_names: ["Acme Co"] } as unknown as Profile;
  const ctx = { profileId: "p1", region: "US" as const, profile };

  it("PUTs scalar suggestions to the profile", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ...profile, legal_name: "Acme Inc" }));
    vi.stubGlobal("fetch", fetchMock);
    const outcome = await applySuggestion(ctx, suggestion("legal_name", "Acme Inc"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [Request | string, RequestInit | undefined];
    const request = url instanceof Request ? url : new Request(url, init);
    expect(request.method).toBe("PUT");
    expect(new URL(request.url).pathname).toBe("/api/v1/profiles/p1");
    expect(await request.json()).toEqual({ legal_name: "Acme Inc" });
    expect(outcome.profile?.legal_name).toBe("Acme Inc");
  });

  it("appends array suggestions without duplicating existing entries", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(profile));
    vi.stubGlobal("fetch", fetchMock);
    await applySuggestion(ctx, suggestion("dba_names[]", ["Acme Co", "ACME"]));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [Request | string, RequestInit | undefined];
    const request = url instanceof Request ? url : new Request(url, init);
    expect(await request.json()).toEqual({ dba_names: ["Acme Co", "ACME"] });
  });

  it("POSTs list suggestions to the matching sub-resource", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ id: "c1" }, 201));
    vi.stubGlobal("fetch", fetchMock);
    const outcome = await applySuggestion(ctx, suggestion("codes.naics[]", "541511"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [Request | string, RequestInit | undefined];
    const request = url instanceof Request ? url : new Request(url, init);
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe("/api/v1/profiles/p1/codes");
    expect(await request.json()).toEqual({ scheme: "naics", code: "541511", is_primary: false });
    expect(outcome.resource).toBe("codes");
  });

  it("refuses region-foreign suggestions", async () => {
    await expect(applySuggestion({ ...ctx, region: "IN" }, suggestion("uei", "X"))).rejects.toThrow(/region IN/);
  });
});
