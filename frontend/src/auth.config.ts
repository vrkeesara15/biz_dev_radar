import type { NextAuthConfig } from "next-auth";

import type { Role } from "@/types/next-auth";

const ROLES: readonly Role[] = [
  "platform_admin",
  "tenant_owner",
  "bid_manager",
  "writer",
  "reviewer",
  "viewer",
];

export type Membership = { tenant_id: string | null; role: Role };

/**
 * TODO(M1): resolve the signed-in user's tenant membership from the backend
 * (GET /api/v1/me or equivalent) and create the user/tenant rows on first sign-in.
 * Until then, claims come from BIDRADAR_DEV_TENANT_ID / BIDRADAR_DEV_ROLE.
 */
export async function resolveMembership(
  email: string | null | undefined,
): Promise<Membership> {
  void email;
  const role = process.env.BIDRADAR_DEV_ROLE;
  return {
    tenant_id: process.env.BIDRADAR_DEV_TENANT_ID ?? null,
    role: ROLES.includes(role as Role) ? (role as Role) : "viewer",
  };
}

/**
 * Edge-safe Auth.js config: no providers that need Node APIs (Nodemailer lives
 * only in src/auth.ts). Used by middleware and spread into the full config.
 */
export const authConfig = {
  session: { strategy: "jwt" },
  pages: { signIn: "/signin", verifyRequest: "/signin/check-email" },
  providers: [],
  callbacks: {
    authorized({ auth }) {
      return Boolean(auth?.user);
    },
    async jwt({ token, user, trigger }) {
      if (user || trigger === "signIn" || token.role === undefined) {
        const membership = await resolveMembership(
          user?.email ?? token.email,
        );
        token.tenant_id = membership.tenant_id;
        token.role = membership.role;
      }
      return token;
    },
    session({ session, token }) {
      session.user.id = token.sub ?? "";
      session.user.tenant_id = token.tenant_id ?? null;
      session.user.role = token.role ?? "viewer";
      return session;
    },
  },
} satisfies NextAuthConfig;
