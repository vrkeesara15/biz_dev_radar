/**
 * Typed API client (openapi-fetch) over the generated OpenAPI schema.
 *
 * Server-side only: the auth middleware calls `getApiToken()` which reads the
 * Auth.js session from request cookies and signs a short-lived HS256 JWT that
 * the backend verifies with the shared AUTH_SECRET.
 */
import createClient, { type Middleware } from "openapi-fetch";

import { getApiToken } from "@/auth";

import type { paths } from "./schema";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = await getApiToken();
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: API_BASE_URL });
api.use(authMiddleware);

export type { paths, components } from "./schema";
