/**
 * Notification preferences (SPEC 7): the event x channel matrix, quiet hours,
 * time zone, digest time and the two minimum scores.
 *
 * Pure logic only — the reducer below is what Settings > Notifications edits and
 * what the vitest suite exercises; `api.ts` does the I/O. Events, channels and
 * the validation rules mirror `backend/app/core/preferences.py` exactly, so an
 * invalid edit is refused in the form rather than by a 422.
 */

/** `NotificationChannel` in the backend enum, in display order. */
export const CHANNELS = ["email", "slack", "teams", "whatsapp", "web_push"] as const;
export type Channel = (typeof CHANNELS)[number];

export const CHANNEL_LABELS: Record<Channel, string> = {
  email: "Email",
  slack: "Slack",
  teams: "Teams",
  whatsapp: "WhatsApp",
  web_push: "Web push",
};

/** `NotificationEvent` in the backend enum, in SPEC 7 table order. */
export const EVENTS = [
  "high_fit_match",
  "digest",
  "amendment",
  "deadline_reminder",
  "pursuit_update",
  "agent_question",
  "approval_request",
  "registration_expiry",
] as const;
export type EventType = (typeof EVENTS)[number];

export type EventMeta = { value: EventType; label: string; description: string };

export const EVENT_META: readonly EventMeta[] = [
  {
    value: "high_fit_match",
    label: "New High-fit match",
    description: "Score ≥ your instant threshold. Instant, outside quiet hours.",
  },
  {
    value: "digest",
    label: "Daily digest",
    description: "Medium matches rolled up at your digest time.",
  },
  {
    value: "amendment",
    label: "Amendment or corrigendum",
    description: "Deadline moved, new document, Q&A or cancellation, with a field-level diff.",
  },
  {
    value: "deadline_reminder",
    label: "Deadline reminder",
    description: "The reminder ladder on a tracked pursuit.",
  },
  { value: "pursuit_update", label: "Pursuit update", description: "Stage changes and assignments." },
  {
    value: "agent_question",
    label: "Agent draft ready or needs input",
    description: "An agent finished a draft or is waiting on an answer.",
  },
  {
    value: "approval_request",
    label: "Approval request",
    description: "A section or final package is waiting for your approval.",
  },
  {
    value: "registration_expiry",
    label: "Registration expiring",
    description: "SAM, DSC, certifications and insurance, 60/30/7 days out.",
  },
] as const;

/** Backend default: email only, for every event. */
export const DEFAULT_CHANNELS_BY_EVENT: Record<EventType, Channel[]> = Object.fromEntries(
  EVENTS.map((event) => [event, ["email"] as Channel[]]),
) as Record<EventType, Channel[]>;

export const DEFAULT_MIN_SCORE_INSTANT = 70;
export const DEFAULT_MIN_SCORE_DIGEST = 50;
export const DEFAULT_DIGEST_TIME = "08:00";

export type PrefsState = {
  channelsByEvent: Record<EventType, Channel[]>;
  quietHoursStart: string | null;
  quietHoursEnd: string | null;
  tz: string;
  digestTime: string;
  minScoreInstant: number;
  minScoreDigest: number;
  /** Email categories the user opted out of from an email footer; read-only here. */
  unsubscribedCategories: string[];
};

/** The wire shape of GET /me/notification-prefs (plus the optional opt-out list). */
export type PrefsWire = {
  channels_by_event?: Record<string, string[]> | null;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  tz?: string | null;
  digest_time?: string | null;
  min_score_instant?: number | null;
  min_score_digest?: number | null;
  unsubscribed_categories?: string[] | null;
};

const isChannel = (value: string): value is Channel => (CHANNELS as readonly string[]).includes(value);
const isEvent = (value: string): value is EventType => (EVENTS as readonly string[]).includes(value);

export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function cleanTime(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return HHMM.test(text) ? text : null;
}

function cleanScore(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(100, Math.max(0, Math.round(n)));
}

/**
 * Reads whatever the API returned into the editor's state: unknown events and
 * channels are dropped, missing events fall back to the server default, and
 * every list keeps `CHANNELS` order so the matrix is stable.
 */
export function normalizePrefs(wire: PrefsWire | null | undefined, browserTz?: string): PrefsState {
  const raw = wire?.channels_by_event ?? {};
  const channelsByEvent = Object.fromEntries(
    EVENTS.map((event) => {
      const chosen = Object.entries(raw).find(([key]) => key === event)?.[1];
      const list = Array.isArray(chosen)
        ? CHANNELS.filter((channel) => chosen.includes(channel))
        : DEFAULT_CHANNELS_BY_EVENT[event];
      return [event, [...list]];
    }),
  ) as Record<EventType, Channel[]>;

  const start = cleanTime(wire?.quiet_hours_start);
  const end = cleanTime(wire?.quiet_hours_end);
  const instant = cleanScore(wire?.min_score_instant, DEFAULT_MIN_SCORE_INSTANT);
  return {
    channelsByEvent,
    // both or neither, as the backend requires
    quietHoursStart: start && end ? start : null,
    quietHoursEnd: start && end ? end : null,
    tz: (typeof wire?.tz === "string" && wire.tz.trim()) || browserTz || "UTC",
    digestTime: cleanTime(wire?.digest_time) ?? DEFAULT_DIGEST_TIME,
    minScoreInstant: instant,
    minScoreDigest: cleanScore(wire?.min_score_digest, DEFAULT_MIN_SCORE_DIGEST),
    unsubscribedCategories: Array.isArray(wire?.unsubscribed_categories)
      ? wire.unsubscribed_categories.filter((c): c is string => typeof c === "string")
      : [],
  };
}

export type PrefsAction =
  | { type: "toggle"; event: EventType; channel: Channel }
  | { type: "setEventChannels"; event: EventType; channels: Channel[] }
  | { type: "setColumn"; channel: Channel; on: boolean }
  | { type: "quietHours"; start: string | null; end: string | null }
  | { type: "tz"; tz: string }
  | { type: "digestTime"; time: string }
  | { type: "minScore"; which: "instant" | "digest"; value: number }
  | { type: "reset"; state: PrefsState };

const order = (channels: Iterable<Channel>) => {
  const set = new Set(channels);
  return CHANNELS.filter((channel) => set.has(channel));
};

/**
 * The matrix reducer. Every change returns a new state; nothing is mutated, so
 * "dirty" is a plain equality check against the last saved state.
 */
export function prefsReducer(state: PrefsState, action: PrefsAction): PrefsState {
  switch (action.type) {
    case "toggle": {
      const current = state.channelsByEvent[action.event] ?? [];
      const next = current.includes(action.channel)
        ? current.filter((channel) => channel !== action.channel)
        : order([...current, action.channel]);
      return { ...state, channelsByEvent: { ...state.channelsByEvent, [action.event]: next } };
    }
    case "setEventChannels":
      return {
        ...state,
        channelsByEvent: { ...state.channelsByEvent, [action.event]: order(action.channels) },
      };
    case "setColumn": {
      const channelsByEvent = Object.fromEntries(
        EVENTS.map((event) => {
          const current = state.channelsByEvent[event] ?? [];
          const next = action.on
            ? order([...current, action.channel])
            : current.filter((channel) => channel !== action.channel);
          return [event, next];
        }),
      ) as Record<EventType, Channel[]>;
      return { ...state, channelsByEvent };
    }
    case "quietHours":
      return { ...state, quietHoursStart: action.start, quietHoursEnd: action.end };
    case "tz":
      return { ...state, tz: action.tz };
    case "digestTime":
      return { ...state, digestTime: action.time };
    case "minScore":
      return action.which === "instant"
        ? { ...state, minScoreInstant: action.value }
        : { ...state, minScoreDigest: action.value };
    case "reset":
      return action.state;
    default:
      return state;
  }
}

/** True when the event sends nothing at all (allowed, but worth saying out loud). */
export const isSilenced = (state: PrefsState, event: EventType) =>
  (state.channelsByEvent[event] ?? []).length === 0;

/**
 * The same checks the backend makes, as messages keyed by form field. An empty
 * object means the state is saveable.
 */
export function validatePrefs(state: PrefsState): Partial<Record<string, string>> {
  const errors: Partial<Record<string, string>> = {};
  const { quietHoursStart: start, quietHoursEnd: end } = state;
  if ((start === null) !== (end === null)) {
    errors.quiet_hours = "Give both a start and an end, or clear both.";
  } else if (start !== null && end !== null) {
    if (!HHMM.test(start) || !HHMM.test(end)) errors.quiet_hours = "Use 24-hour HH:MM.";
    else if (start === end) errors.quiet_hours = "Quiet hours must not start and end at the same time.";
  }
  if (!HHMM.test(state.digestTime)) errors.digest_time = "Use 24-hour HH:MM.";
  if (!state.tz.trim()) errors.tz = "Choose a time zone.";
  for (const [key, value] of [
    ["min_score_instant", state.minScoreInstant],
    ["min_score_digest", state.minScoreDigest],
  ] as const) {
    if (!Number.isInteger(value) || value < 0 || value > 100) errors[key] = "0 to 100.";
  }
  if (!errors.min_score_instant && !errors.min_score_digest && state.minScoreDigest > state.minScoreInstant) {
    errors.min_score_digest = "The digest threshold cannot exceed the instant threshold.";
  }
  return errors;
}

/** Body for PUT /me/notification-prefs (the route forbids unknown fields). */
export function toPrefsBody(state: PrefsState) {
  return {
    channels_by_event: Object.fromEntries(EVENTS.map((event) => [event, state.channelsByEvent[event] ?? []])),
    quiet_hours_start: state.quietHoursStart,
    quiet_hours_end: state.quietHoursEnd,
    tz: state.tz,
    digest_time: state.digestTime,
    min_score_instant: state.minScoreInstant,
    min_score_digest: state.minScoreDigest,
  };
}

/** Structural equality, for the "you have unsaved changes" state. */
export function samePrefs(a: PrefsState, b: PrefsState): boolean {
  return JSON.stringify(toPrefsBody(a)) === JSON.stringify(toPrefsBody(b));
}

/** Label for an unsubscribed category ("all" is not an event). */
export function categoryLabel(category: string): string {
  if (category === "all") return "Every category";
  const meta = EVENT_META.find((event) => event.value === category);
  return meta?.label ?? category.replace(/_/g, " ");
}

export { isChannel, isEvent };
