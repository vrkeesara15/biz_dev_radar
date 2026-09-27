/**
 * Mints an Auth.js v5 session cookie for Playwright without a mail server.
 *
 * Auth.js encrypts JWT sessions (JWE) with a key derived from AUTH_SECRET and
 * the cookie name as salt; the dev server under test is started with the same
 * secret (see playwright.config.ts), so the middleware and `auth()` accept it.
 * No application code is bypassed.
 */
import { encode } from "@auth/core/jwt";
import type { BrowserContext } from "@playwright/test";

export const E2E_AUTH_SECRET = process.env.AUTH_SECRET ?? "e2e-only-secret-0123456789abcdef0123456789abcdef";
export const SESSION_COOKIE = "authjs.session-token";

export const E2E_USER = {
  sub: "00000000-0000-0000-0000-0000000000e2",
  email: "e2e@example.com",
  name: "E2E Owner",
  tenant_id: "00000000-0000-0000-0000-000000000001",
  role: "tenant_owner",
};

export async function signInAs(context: BrowserContext, baseURL: string) {
  const token = await encode({
    token: E2E_USER,
    secret: E2E_AUTH_SECRET,
    salt: SESSION_COOKIE,
    maxAge: 60 * 60,
  });
  const url = new URL(baseURL);
  await context.addCookies([
    {
      name: SESSION_COOKIE,
      value: token,
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
      secure: url.protocol === "https:",
    },
  ]);
}
