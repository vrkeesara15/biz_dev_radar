import type { DefaultSession } from "next-auth";

/** Roles from SPEC.md §3. */
export type Role =
  | "platform_admin"
  | "tenant_owner"
  | "bid_manager"
  | "writer"
  | "reviewer"
  | "viewer";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      tenant_id: string | null;
      role: Role;
    } & DefaultSession["user"];
  }
}

// next-auth/jwt is a bare `export *` re-export, which TS augmentation cannot
// merge through, so the JWT claims are augmented on @auth/core/jwt directly.
declare module "@auth/core/jwt" {
  interface JWT {
    tenant_id?: string | null;
    role?: Role;
  }
}
