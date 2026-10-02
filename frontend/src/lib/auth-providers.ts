/**
 * Which sign-in methods are configured, derived purely from env so this module
 * is safe to import from any runtime (edge middleware, server components, tests).
 */
import { devLoginAllowlist, devLoginEnabled } from "./auth-dev-login";

export type ProviderAvailability = {
  email: boolean;
  google: boolean;
  microsoft: boolean;
  /** Demo-only credentials sign-in; see src/lib/auth-dev-login.ts. */
  devLogin: boolean;
};

export function getProviderAvailability(
  env: NodeJS.ProcessEnv = process.env,
): ProviderAvailability {
  return {
    email: Boolean(env.EMAIL_SERVER && env.EMAIL_FROM),
    google: Boolean(env.AUTH_GOOGLE_ID && env.AUTH_GOOGLE_SECRET),
    microsoft: Boolean(
      env.AUTH_MICROSOFT_ENTRA_ID_ID && env.AUTH_MICROSOFT_ENTRA_ID_SECRET,
    ),
    // Both halves are required: the flag alone, with no allowlist, would register
    // a provider that can never authorise anybody, and show a form that always fails.
    devLogin: devLoginEnabled(env) && devLoginAllowlist(env).length > 0,
  };
}
