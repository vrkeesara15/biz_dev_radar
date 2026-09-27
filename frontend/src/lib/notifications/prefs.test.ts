import { describe, expect, it } from "vitest";

import {
  CHANNELS,
  DEFAULT_DIGEST_TIME,
  EVENTS,
  categoryLabel,
  isSilenced,
  normalizePrefs,
  prefsReducer,
  samePrefs,
  toPrefsBody,
  validatePrefs,
  type PrefsState,
} from "./prefs";

const base = (): PrefsState => normalizePrefs(null, "Asia/Kolkata");

describe("normalizePrefs", () => {
  it("falls back to the backend defaults when the API has nothing", () => {
    const state = base();
    expect(state.tz).toBe("Asia/Kolkata");
    expect(state.digestTime).toBe(DEFAULT_DIGEST_TIME);
    expect(state.minScoreInstant).toBe(70);
    expect(state.minScoreDigest).toBe(50);
    expect(state.channelsByEvent.high_fit_match).toEqual(["email"]);
    expect(Object.keys(state.channelsByEvent)).toEqual([...EVENTS]);
  });

  it("drops unknown events and channels and keeps the channel order", () => {
    const state = normalizePrefs({
      channels_by_event: {
        high_fit_match: ["web_push", "email", "carrier_pigeon"],
        unknown_event: ["email"],
      },
      tz: "America/New_York",
    });
    expect(state.channelsByEvent.high_fit_match).toEqual(["email", "web_push"]);
    expect("unknown_event" in state.channelsByEvent).toBe(false);
    expect(state.channelsByEvent.digest).toEqual(["email"]);
  });

  it("keeps quiet hours only when both ends are valid", () => {
    expect(normalizePrefs({ quiet_hours_start: "21:00", quiet_hours_end: "07:30" }).quietHoursStart).toBe("21:00");
    expect(normalizePrefs({ quiet_hours_start: "21:00", quiet_hours_end: null }).quietHoursStart).toBeNull();
    expect(normalizePrefs({ quiet_hours_start: "9pm", quiet_hours_end: "07:30" }).quietHoursEnd).toBeNull();
  });

  it("clamps the scores into 0..100", () => {
    const state = normalizePrefs({ min_score_instant: 140, min_score_digest: -5 });
    expect(state.minScoreInstant).toBe(100);
    expect(state.minScoreDigest).toBe(0);
  });

  it("reads the email opt-out list when the API sends one", () => {
    expect(normalizePrefs({ unsubscribed_categories: ["digest", 7 as unknown as string] }).unsubscribedCategories).toEqual([
      "digest",
    ]);
  });
});

describe("prefsReducer", () => {
  it("toggles one cell on and off without touching the rest", () => {
    const state = base();
    const on = prefsReducer(state, { type: "toggle", event: "high_fit_match", channel: "slack" });
    expect(on.channelsByEvent.high_fit_match).toEqual(["email", "slack"]);
    expect(on.channelsByEvent.digest).toEqual(["email"]);
    expect(state.channelsByEvent.high_fit_match).toEqual(["email"]);
    const off = prefsReducer(on, { type: "toggle", event: "high_fit_match", channel: "email" });
    expect(off.channelsByEvent.high_fit_match).toEqual(["slack"]);
  });

  it("keeps CHANNELS order however the cells are clicked", () => {
    let state = base();
    for (const channel of ["web_push", "teams", "whatsapp"] as const) {
      state = prefsReducer(state, { type: "toggle", event: "amendment", channel });
    }
    expect(state.channelsByEvent.amendment).toEqual(["email", "teams", "whatsapp", "web_push"]);
  });

  it("sets and clears a whole column", () => {
    const all = prefsReducer(base(), { type: "setColumn", channel: "slack", on: true });
    expect(EVENTS.every((event) => all.channelsByEvent[event].includes("slack"))).toBe(true);
    const none = prefsReducer(all, { type: "setColumn", channel: "email", on: false });
    expect(EVENTS.every((event) => !none.channelsByEvent[event].includes("email"))).toBe(true);
    expect(none.channelsByEvent.digest).toEqual(["slack"]);
  });

  it("can silence an event entirely", () => {
    const silenced = prefsReducer(base(), { type: "setEventChannels", event: "digest", channels: [] });
    expect(isSilenced(silenced, "digest")).toBe(true);
    expect(isSilenced(silenced, "high_fit_match")).toBe(false);
  });

  it("edits the scalar fields", () => {
    let state = base();
    state = prefsReducer(state, { type: "quietHours", start: "22:00", end: "06:00" });
    state = prefsReducer(state, { type: "tz", tz: "Europe/London" });
    state = prefsReducer(state, { type: "digestTime", time: "07:15" });
    state = prefsReducer(state, { type: "minScore", which: "instant", value: 80 });
    state = prefsReducer(state, { type: "minScore", which: "digest", value: 60 });
    expect(toPrefsBody(state)).toMatchObject({
      quiet_hours_start: "22:00",
      quiet_hours_end: "06:00",
      tz: "Europe/London",
      digest_time: "07:15",
      min_score_instant: 80,
      min_score_digest: 60,
    });
  });
});

describe("validatePrefs", () => {
  it("accepts the defaults", () => {
    expect(validatePrefs(base())).toEqual({});
  });

  it("requires both quiet-hours ends, and that they differ", () => {
    const half = prefsReducer(base(), { type: "quietHours", start: "22:00", end: null });
    expect(validatePrefs(half).quiet_hours).toMatch(/both/i);
    const same = prefsReducer(base(), { type: "quietHours", start: "22:00", end: "22:00" });
    expect(validatePrefs(same).quiet_hours).toMatch(/same time/i);
    const wrapping = prefsReducer(base(), { type: "quietHours", start: "22:00", end: "06:00" });
    expect(validatePrefs(wrapping).quiet_hours).toBeUndefined();
  });

  it("refuses a digest threshold above the instant threshold, as the API does", () => {
    const state = prefsReducer(base(), { type: "minScore", which: "digest", value: 90 });
    expect(validatePrefs(state).min_score_digest).toMatch(/cannot exceed/i);
  });

  it("refuses a score outside 0..100 and a malformed digest time", () => {
    const score = prefsReducer(base(), { type: "minScore", which: "instant", value: 101 });
    expect(validatePrefs(score).min_score_instant).toBeDefined();
    const time = prefsReducer(base(), { type: "digestTime", time: "25:00" });
    expect(validatePrefs(time).digest_time).toBeDefined();
  });
});

describe("toPrefsBody / samePrefs", () => {
  it("sends every event, even the silenced ones", () => {
    const body = toPrefsBody(prefsReducer(base(), { type: "setEventChannels", event: "digest", channels: [] }));
    expect(Object.keys(body.channels_by_event)).toEqual([...EVENTS]);
    expect(body.channels_by_event.digest).toEqual([]);
  });

  it("ignores the read-only opt-out list when comparing", () => {
    const a = base();
    const b = { ...a, unsubscribedCategories: ["digest"] };
    expect(samePrefs(a, b)).toBe(true);
    expect(samePrefs(a, prefsReducer(a, { type: "tz", tz: "UTC" }))).toBe(false);
  });
});

describe("categoryLabel", () => {
  it("names the event categories and the catch-all", () => {
    expect(categoryLabel("all")).toBe("Every category");
    expect(categoryLabel("high_fit_match")).toBe("New High-fit match");
    expect(categoryLabel("something_new")).toBe("something new");
  });
});

describe("the matrix vocabulary", () => {
  it("matches the backend enums", () => {
    expect([...CHANNELS]).toEqual(["email", "slack", "teams", "whatsapp", "web_push"]);
    expect([...EVENTS]).toEqual([
      "high_fit_match",
      "digest",
      "amendment",
      "deadline_reminder",
      "pursuit_update",
      "agent_question",
      "approval_request",
      "registration_expiry",
    ]);
  });
});
