import { defineConfig } from "@playwright/test";

/**
 * E2E runs against `next dev` on its own port with a known AUTH_SECRET so
 * tests can mint a session cookie (see e2e/auth.ts). The backend is never
 * needed: specs stub `/api/v1/**` with page.route.
 */
const port = Number(process.env.E2E_PORT ?? 3100);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${port}`;
const authSecret = process.env.AUTH_SECRET ?? "e2e-only-secret-0123456789abcdef0123456789abcdef";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  timeout: 90_000,
  use: { baseURL, trace: "on-first-retry" },
  webServer: {
    command: `pnpm dev --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      AUTH_SECRET: authSecret,
      AUTH_TRUST_HOST: "true",
      AUTH_URL: baseURL,
      NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
    },
  },
});
