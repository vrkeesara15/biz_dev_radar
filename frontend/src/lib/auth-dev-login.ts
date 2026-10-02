/**
 * Demo-only sign-in, so a deployment with no OAuth client and no SMTP is not a
 * sign-in page where every control is disabled.
 *
 * **This is not authentication.** It asks for an email, checks it against an
 * allowlist, and issues a session. There is no password, no second factor and no
 * proof the person owns the address. It exists for a demo on throwaway data and
 * it must never be on in front of anything real — see the guard rails:
 *
 *   1. `AUTH_DEV_LOGIN=1` has to be set explicitly. Unset, nothing is registered
 *      at all: the provider does not appear in `/api/auth/providers`, the form is
 *      not rendered, and a POST to the callback 404s.
 *   2. `AUTH_DEV_LOGIN_EMAILS` has to list the addresses, exactly. An empty or
 *      missing list means the gate is open to nobody, not to everybody.
 *   3. The sign-in page shows a "demo mode" badge whenever it is live, so nobody
 *      can look at the screen and not know.
 *
 * Replace it with Google or Microsoft (`AUTH_GOOGLE_ID`/`SECRET`,
 * `AUTH_MICROSOFT_ENTRA_ID_ID`/`SECRET`) before a single real tenant signs in.
 *
 * Pure and dependency-free so it can be imported from any runtime and tested
 * without Auth.js: `crypto.subtle` and `TextEncoder` are Web APIs present in both
 * the Node and the edge runtime.
 */

/** The Auth.js provider id. Also the value `signIn()` and the callback URL use. */
export const DEV_LOGIN_PROVIDER_ID = "dev-login";

/**
 * Namespace for the deterministic user ids below. A fixed random uuid, so the id
 * for an address is stable across restarts and redeploys (the session's `sub`,
 * and therefore the backend JWT's subject, does not change under the user).
 */
const DEV_LOGIN_NAMESPACE = "6f0c2a1e-5a4d-4a3e-9c0b-2f1d7a8e4b53";

export type DevLoginUser = { id: string; email: string; name: string };

export function devLoginEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.AUTH_DEV_LOGIN === "1";
}

/** The allowlist, lowercased and de-blanked. Empty when the variable is unset. */
export function devLoginAllowlist(
  env: NodeJS.ProcessEnv = process.env,
): string[] {
  return (env.AUTH_DEV_LOGIN_EMAILS ?? "")
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);
}

function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** RFC 4122 §4.3 name-based uuid, version 5 (SHA-1 over namespace ‖ name). */
export async function deterministicUserId(email: string): Promise<string> {
  const namespace = Uint8Array.from(
    DEV_LOGIN_NAMESPACE.replace(/-/g, "").match(/../g)!.map((b) => parseInt(b, 16)),
  );
  const name = new TextEncoder().encode(email);
  const input = new Uint8Array(namespace.length + name.length);
  input.set(namespace);
  input.set(name, namespace.length);

  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", input));
  const bytes = digest.slice(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50; // version 5
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const s = hex(bytes);
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
}

/**
 * The provider's `authorize`. Returns a user for an allowlisted address and
 * `null` for everything else — including every address when the gate is off, so
 * a stale provider registration cannot let anyone in.
 */
export async function authorizeDevLogin(
  credentials: Partial<Record<string, unknown>> | undefined,
  env: NodeJS.ProcessEnv = process.env,
): Promise<DevLoginUser | null> {
  if (!devLoginEnabled(env)) return null;
  const raw = credentials?.email;
  if (typeof raw !== "string") return null;
  const email = raw.trim().toLowerCase();
  if (!email) return null;
  if (!devLoginAllowlist(env).includes(email)) return null;
  return {
    id: await deterministicUserId(email),
    email,
    name: email.split("@")[0],
  };
}
