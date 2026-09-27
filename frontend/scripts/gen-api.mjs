#!/usr/bin/env node
/**
 * Regenerates src/lib/api/schema.d.ts from the backend OpenAPI spec.
 *
 * Source precedence:
 *   1. ../backend/openapi.json (committed/exported spec) when it exists
 *   2. $API_URL/openapi.json   (default http://localhost:8000)
 */
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(here, "..");
const localSpec = path.resolve(frontendDir, "..", "backend", "openapi.json");
const apiUrl = (process.env.API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const input = existsSync(localSpec) ? localSpec : `${apiUrl}/openapi.json`;
const output = path.join("src", "lib", "api", "schema.d.ts");

console.log(`gen:api  ${input} -> ${output}`);

const result = spawnSync(
  "pnpm",
  ["exec", "openapi-typescript", input, "-o", output, "--alphabetize"],
  { cwd: frontendDir, stdio: "inherit" },
);
if (result.status !== 0) {
  console.error(
    "gen:api failed. Export the spec to ../backend/openapi.json or start the backend and set API_URL.",
  );
  process.exit(result.status ?? 1);
}
