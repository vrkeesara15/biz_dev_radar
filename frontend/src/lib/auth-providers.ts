/**
 * Which sign-in methods are configured, derived purely from env so this module
 * is safe to import from any runtime (edge middleware, server components, tests).
 */
export type ProviderAvailability = {
  email: boolean;
  google: boolean;
  microsoft: boolean;
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
  };
}
