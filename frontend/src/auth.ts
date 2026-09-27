import { SignJWT } from "jose";
import NextAuth, { type NextAuthConfig } from "next-auth";
import Google from "next-auth/providers/google";
import MicrosoftEntraID from "next-auth/providers/microsoft-entra-id";
import Nodemailer from "next-auth/providers/nodemailer";

import { authConfig } from "@/auth.config";
import { createMemoryAdapter } from "@/lib/auth-adapter";
import { getProviderAvailability } from "@/lib/auth-providers";

/**
 * Providers are only registered when their env is present so `next build`
 * and local dev never crash on missing OAuth credentials. Google and
 * Microsoft read AUTH_GOOGLE_ID/SECRET and AUTH_MICROSOFT_ENTRA_ID_ID/SECRET/
 * ISSUER automatically from env (Auth.js convention).
 */
function buildProviders(): NextAuthConfig["providers"] {
  const available = getProviderAvailability();
  const providers: NextAuthConfig["providers"] = [];
  if (available.email) {
    providers.push(
      Nodemailer({
        server: process.env.EMAIL_SERVER,
        from: process.env.EMAIL_FROM,
      }),
    );
  }
  if (available.google) providers.push(Google);
  if (available.microsoft) providers.push(MicrosoftEntraID);
  return providers;
}

/**
 * The email provider needs an adapter to hold one-time verification tokens.
 * Until the backend user store exists this is a process-local memory adapter
 * (see src/lib/auth-adapter.ts); it is attached only when email sign-in is
 * configured so OAuth-only deployments keep stateless JWT behaviour.
 */
export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  providers: buildProviders(),
  adapter: getProviderAvailability().email ? createMemoryAdapter() : undefined,
});

/** Lifetime of the API bearer token minted per server request. */
export const API_TOKEN_TTL = "15m";

/**
 * Signs an HS256 JWT ({sub, email, tenant_id, role, exp}) with AUTH_SECRET for
 * the backend, which verifies it with the same shared secret. Returns null
 * when there is no session.
 */
export async function getApiToken(): Promise<string | null> {
  const session = await auth();
  if (!session?.user) return null;
  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new Error("AUTH_SECRET is not set");
  return new SignJWT({
    email: session.user.email ?? null,
    tenant_id: session.user.tenant_id,
    role: session.user.role,
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(session.user.id)
    .setIssuedAt()
    .setExpirationTime(API_TOKEN_TTL)
    .sign(new TextEncoder().encode(secret));
}
