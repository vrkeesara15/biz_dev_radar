/**
 * OQ-77: the backend origin is resolved at runtime, not baked into the bundle.
 *
 * `@/auth` is mocked because importing the real module pulls Auth.js and Nodemailer
 * into a unit test that only cares about one string.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({ getApiToken: async () => null }));

async function resolve(): Promise<string> {
  vi.resetModules();
  const { resolveApiBaseUrl } = await import("./client");
  return resolveApiBaseUrl();
}

describe("resolveApiBaseUrl", () => {
  const saved = { api: process.env.API_URL, pub: process.env.NEXT_PUBLIC_API_URL };

  beforeEach(() => {
    delete process.env.API_URL;
    delete process.env.NEXT_PUBLIC_API_URL;
  });

  afterEach(() => {
    if (saved.api === undefined) delete process.env.API_URL;
    else process.env.API_URL = saved.api;
    if (saved.pub === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = saved.pub;
  });

  it("falls back to localhost when nothing is set", async () => {
    await expect(resolve()).resolves.toBe("http://localhost:8000");
  });

  it("uses NEXT_PUBLIC_API_URL when that is all there is (the build-time bake)", async () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.example.test";
    await expect(resolve()).resolves.toBe("https://api.example.test");
  });

  it("prefers the runtime API_URL over the value baked at build time", async () => {
    process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
    process.env.API_URL = "http://api.railway.internal:8080";
    await expect(resolve()).resolves.toBe("http://api.railway.internal:8080");
  });

  it("exports the module-load snapshot as API_BASE_URL", async () => {
    process.env.API_URL = "http://api.railway.internal:8080";
    vi.resetModules();
    const { API_BASE_URL } = await import("./client");
    expect(API_BASE_URL).toBe("http://api.railway.internal:8080");
  });
});
