/**
 * Same-origin proxy for the backend API used by client components.
 *
 * Browser code calls `/api/v1/...` on this origin; this handler attaches the
 * short-lived bearer token minted from the Auth.js session (`getApiToken`) and
 * forwards the request verbatim to `${resolveApiBaseUrl()}/api/v1/...`. Keeping the
 * token server-side means the browser never sees AUTH_SECRET-derived JWTs and
 * the backend CORS list stays untouched. Playwright stubs `/api/v1/**` here.
 */
import { NextRequest, NextResponse } from "next/server";

import { getApiToken } from "@/auth";
import { resolveApiBaseUrl } from "@/lib/api/client";

export const dynamic = "force-dynamic";

const HOP_BY_HOP = new Set([
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "transfer-encoding",
  "cookie",
]);

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const token = await getApiToken();
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  const { path } = await context.params;
  // Resolved per request, not at module load: `API_URL` is a plain runtime variable
  // (OQ-77), so a platform can change it without rebuilding the image.
  const target = new URL(`${resolveApiBaseUrl()}/api/v1/${path.join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set("Authorization", `Bearer ${token}`);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.arrayBuffer() : undefined;

  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body,
    redirect: "manual",
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) responseHeaders.set(key, value);
  });
  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export {
  proxy as GET,
  proxy as POST,
  proxy as PUT,
  proxy as PATCH,
  proxy as DELETE,
};
