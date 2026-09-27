#!/usr/bin/env node
/**
 * Runs Playwright, but skips gracefully (exit 0 with a clear message) when the
 * Chromium browser has not been installed yet, so `pnpm e2e` never fails a
 * machine that simply lacks the download. Install with:
 *   pnpm exec playwright install chromium
 * Set E2E_REQUIRE_BROWSER=1 to fail instead of skipping (CI).
 */
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { chromium } from "@playwright/test";

let executable = null;
try {
  executable = chromium.executablePath();
} catch {
  executable = null;
}

if (!executable || !existsSync(executable)) {
  const message =
    "pnpm e2e: Playwright Chromium is not installed; skipping e2e specs.\n" +
    "         Run `pnpm exec playwright install chromium` and re-run `pnpm e2e`.";
  if (process.env.E2E_REQUIRE_BROWSER) {
    console.error(message);
    process.exit(1);
  }
  console.warn(message);
  process.exit(0);
}

const result = spawnSync("pnpm", ["exec", "playwright", "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  env: process.env,
});
process.exit(result.status ?? 1);
