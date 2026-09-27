import NextAuth from "next-auth";

import { authConfig } from "@/auth.config";

/** Protects /app/*; unauthenticated requests are redirected to /signin. */
export const { auth: middleware } = NextAuth(authConfig);

export const config = {
  matcher: ["/app/:path*"],
};
