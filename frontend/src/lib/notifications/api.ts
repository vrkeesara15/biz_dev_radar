/**
 * Typed wrappers over the in-app bell, web push and notification preferences
 * (SPEC 7, routes M4-11/M4-12). Everything goes through the same-origin proxy
 * (`src/app/api/v1/[...path]`), which attaches the session's bearer token.
 *
 * `payload` on a notification is jsonb, so the view model below reads it
 * defensively: a bell item always renders, even if a future event type carries
 * fields this build has never seen.
 */
import { browserApi, type Schemas } from "@/lib/api/browser";
import { ApiError, NotAvailableError } from "@/lib/opportunities/api";

import type { PrefsWire } from "./prefs";

export type NotificationOut = Schemas["NotificationOut"];
export type NotificationPage = Schemas["NotificationPage"];
export type NotificationPrefs = Schemas["NotificationPrefsOut"];

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) {
    if (response.status === 404) throw new NotAvailableError(error);
    throw new ApiError(response.status, error);
  }
  return data as T;
}

export const listNotifications = (
  query: { unread?: boolean; limit?: number } = {},
  signal?: AbortSignal,
) =>
  unwrap(
    browserApi.GET("/api/v1/me/notifications", {
      params: { query: query as never },
      signal,
    }),
  );

export const markNotificationRead = (notificationId: string) =>
  unwrap(
    browserApi.POST("/api/v1/me/notifications/{notification_id}/read", {
      params: { path: { notification_id: notificationId } },
    }),
  );

export const markAllNotificationsRead = () =>
  unwrap(browserApi.POST("/api/v1/me/notifications/read-all", {}));

export const getNotificationPrefs = (signal?: AbortSignal) =>
  unwrap(browserApi.GET("/api/v1/me/notification-prefs", { signal })) as Promise<
    NotificationPrefs & PrefsWire
  >;

export const putNotificationPrefs = (body: Schemas["NotificationPrefsIn"]) =>
  unwrap(browserApi.PUT("/api/v1/me/notification-prefs", { body })) as Promise<
    NotificationPrefs & PrefsWire
  >;

export const savePushSubscription = (body: Schemas["PushSubscriptionIn"]) =>
  unwrap(browserApi.POST("/api/v1/me/push-subscriptions", { body }));

export const deletePushSubscription = (endpoint: string) =>
  unwrap(browserApi.DELETE("/api/v1/me/push-subscriptions", { body: { endpoint } }));

// --- the bell's view model ---------------------------------------------------

export type NotificationAction = { action: string; url: string };

export type BellItem = {
  id: string;
  eventType: string;
  title: string;
  detail: string | null;
  /** In-app path from the payload's absolute `deep_link`, or null. */
  href: string | null;
  createdAt: string;
  readAt: string | null;
  score: number | null;
  actions: NotificationAction[];
  actionsTaken: string[];
};

const EVENT_TITLES: Record<string, string> = {
  high_fit_match: "New High-fit match",
  digest: "Your digest",
  amendment: "Amendment on a tracked opportunity",
  deadline_reminder: "Deadline reminder",
  pursuit_update: "Pursuit update",
  agent_question: "An agent needs input",
  approval_request: "Approval request",
  registration_expiry: "Registration expiring",
};

export const ACTION_LABELS: Record<string, string> = {
  pursue: "Pursue",
  watch: "Watch",
  pass: "Pass",
  assign: "Assign",
};

const str = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

/**
 * Turns an absolute deep link into an in-app path. Notifications are minted
 * with the API's `APP_BASE_URL`, which in dev and in preview environments is
 * not the origin the browser is on, so only the path is kept.
 */
export function toAppPath(link: unknown): string | null {
  const value = str(link);
  if (!value) return null;
  if (value.startsWith("/")) return value;
  try {
    const url = new URL(value);
    return `${url.pathname}${url.search}`;
  } catch {
    return null;
  }
}

/**
 * One-click action URLs are absolute API links; rewrite them onto this origin
 * so the same-origin proxy can add the bearer token. A link to some other host
 * is dropped rather than followed.
 */
export function toActionPath(link: unknown): string | null {
  const value = str(link);
  if (!value) return null;
  if (value.startsWith("/api/v1/")) return value;
  try {
    const url = new URL(value);
    return url.pathname.startsWith("/api/v1/") ? `${url.pathname}${url.search}` : null;
  } catch {
    return null;
  }
}

export function eventTitle(eventType: string, payload: Record<string, unknown>): string {
  return str(payload.title) ?? EVENT_TITLES[eventType] ?? eventType.replace(/_/g, " ");
}

export function toBellItem(row: NotificationOut): BellItem {
  const payload = (row.payload ?? {}) as Record<string, unknown>;
  const rawActions = (payload.actions ?? {}) as Record<string, unknown>;
  const actions: NotificationAction[] = Object.entries(rawActions)
    .map(([action, url]) => ({ action, url: toActionPath(url) ?? "" }))
    .filter((entry) => entry.url !== "")
    .sort((a, b) => {
      const order = Object.keys(ACTION_LABELS);
      return order.indexOf(a.action) - order.indexOf(b.action);
    });
  const score = Number(payload.score);
  const detailParts = [str(payload.buyer), str(payload.band), str(payload.summary)].filter(Boolean);
  return {
    id: row.id,
    eventType: row.event_type,
    title: eventTitle(row.event_type, payload),
    detail: detailParts.length ? detailParts.join(" · ") : null,
    href: toAppPath(payload.deep_link),
    createdAt: row.created_at,
    readAt: row.read_at,
    score: Number.isFinite(score) ? score : null,
    actions,
    actionsTaken: Array.isArray(payload.actions_taken)
      ? payload.actions_taken
          .map((entry) =>
            typeof entry === "string" ? entry : str((entry as { action?: unknown })?.action),
          )
          .filter((entry): entry is string => !!entry)
      : [],
  };
}

/** "3m ago", "2h ago", "Sep 12" — short enough for a dropdown row. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.round((now.getTime() - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** How often the bell re-reads the unread count (SPEC 7: within 5 minutes). */
export const POLL_INTERVAL_MS = 60_000;
