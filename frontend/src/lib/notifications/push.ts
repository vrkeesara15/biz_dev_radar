/**
 * Web push opt-in (SPEC 7, channel `web_push`).
 *
 * The browser half: register the service worker in `public/sw.js`, ask for the
 * Notification permission, subscribe with the VAPID public key and hand the
 * subscription to POST /me/push-subscriptions. Every step can legitimately be
 * unavailable (Safari without a home-screen install, an insecure origin, a
 * denied permission, no key configured), so `pushState()` answers with a reason
 * the button can show instead of failing.
 *
 * The key comes from NEXT_PUBLIC_VAPID_PUBLIC_KEY, which Next inlines at build
 * time (see OQ-77): the same caveat as the API base URL.
 */
import { deletePushSubscription, savePushSubscription } from "./api";

export const SERVICE_WORKER_PATH = "/sw.js";

export type PushStatus =
  | { kind: "unsupported"; reason: string }
  | { kind: "unconfigured"; reason: string }
  | { kind: "denied"; reason: string }
  | { kind: "off" }
  | { kind: "on"; endpoint: string };

export function vapidPublicKey(): string {
  return (process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ?? "").trim();
}

/** RFC 8292 keys travel as base64url; `pushManager.subscribe` wants bytes. */
export function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padded = base64.trim().replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** base64url of an ArrayBuffer, the shape the API stores subscription keys in. */
export function arrayBufferToBase64Url(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** Current state, without asking for anything. */
export async function pushState(): Promise<PushStatus> {
  if (!pushSupported()) {
    return {
      kind: "unsupported",
      reason: "This browser cannot receive web push. Email and Slack still work.",
    };
  }
  if (!vapidPublicKey()) {
    return { kind: "unconfigured", reason: "Web push is not configured for this deployment." };
  }
  if (Notification.permission === "denied") {
    return {
      kind: "denied",
      reason: "Notifications are blocked for this site in your browser settings.",
    };
  }
  const registration = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_PATH);
  const existing = await registration?.pushManager.getSubscription();
  return existing ? { kind: "on", endpoint: existing.endpoint } : { kind: "off" };
}

export class PushError extends Error {}

/** Register, ask, subscribe, store. Returns the new state. */
export async function enablePush(): Promise<PushStatus> {
  const state = await pushState();
  if (state.kind !== "off") return state;
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { kind: "denied", reason: "Permission was not granted, so nothing is sent." };
  }
  const registration = await navigator.serviceWorker.register(SERVICE_WORKER_PATH);
  await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey()) as BufferSource,
  });
  const p256dh = arrayBufferToBase64Url(subscription.getKey("p256dh"));
  const auth = arrayBufferToBase64Url(subscription.getKey("auth"));
  if (!p256dh || !auth) {
    await subscription.unsubscribe();
    throw new PushError("The browser returned a subscription without keys.");
  }
  await savePushSubscription({ endpoint: subscription.endpoint, keys: { p256dh, auth } });
  return { kind: "on", endpoint: subscription.endpoint };
}

/** Drop the subscription here and on the server. */
export async function disablePush(): Promise<PushStatus> {
  if (!pushSupported()) return { kind: "unsupported", reason: "This browser cannot receive web push." };
  const registration = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_PATH);
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return { kind: "off" };
  const { endpoint } = subscription;
  await subscription.unsubscribe();
  try {
    await deletePushSubscription(endpoint);
  } catch {
    // A subscription the server never stored is already gone; the browser is
    // the source of truth for "this device no longer receives push".
  }
  return { kind: "off" };
}
