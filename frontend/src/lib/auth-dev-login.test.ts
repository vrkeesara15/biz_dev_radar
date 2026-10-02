/**
 * The demo sign-in is the only provider that issues a session without proving the
 * person owns the address, so every one of its gates gets a test. A regression here
 * is not a broken form — it is an open door.
 */
import { describe, expect, it } from "vitest";

import {
  DEV_LOGIN_PROVIDER_ID,
  authorizeDevLogin,
  deterministicUserId,
  devLoginAllowlist,
  devLoginEnabled,
} from "./auth-dev-login";
import { getProviderAvailability } from "./auth-providers";

/** A ProcessEnv holding only the keys a case cares about (NODE_ENV is required). */
function env(vars: Record<string, string>): NodeJS.ProcessEnv {
  return { NODE_ENV: "test", ...vars } as NodeJS.ProcessEnv;
}

const ON = env({
  AUTH_DEV_LOGIN: "1",
  AUTH_DEV_LOGIN_EMAILS: "owner@example.com, Second@Example.com",
});

describe("the gate", () => {
  it("is off unless AUTH_DEV_LOGIN is exactly 1", () => {
    expect(devLoginEnabled(env({}))).toBe(false);
    expect(devLoginEnabled(env({ AUTH_DEV_LOGIN: "" }))).toBe(false);
    expect(devLoginEnabled(env({ AUTH_DEV_LOGIN: "0" }))).toBe(false);
    expect(devLoginEnabled(env({ AUTH_DEV_LOGIN: "true" }))).toBe(false);
    expect(devLoginEnabled(env({ AUTH_DEV_LOGIN: "1" }))).toBe(true);
  });

  it("parses the allowlist, lowercasing and dropping blanks", () => {
    expect(devLoginAllowlist(ON)).toEqual(["owner@example.com", "second@example.com"]);
    expect(devLoginAllowlist(env({}))).toEqual([]);
    expect(
      devLoginAllowlist(env({ AUTH_DEV_LOGIN_EMAILS: " , ,a@b.c , " })),
    ).toEqual(["a@b.c"]);
  });

  it("reports the provider as available only when BOTH halves are set", () => {
    expect(getProviderAvailability(ON).devLogin).toBe(true);
    expect(
      getProviderAvailability(env({ AUTH_DEV_LOGIN: "1" })).devLogin,
    ).toBe(false);
    expect(getProviderAvailability(
      env({ AUTH_DEV_LOGIN_EMAILS: "owner@example.com" }),
    ).devLogin).toBe(false);
  });
});

describe("authorizeDevLogin", () => {
  it("accepts an allowlisted address", async () => {
    const user = await authorizeDevLogin({ email: "owner@example.com" }, ON);
    expect(user).not.toBeNull();
    expect(user!.email).toBe("owner@example.com");
    expect(user!.name).toBe("owner");
    expect(user!.id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("matches case-insensitively and ignores surrounding space", async () => {
    const a = await authorizeDevLogin({ email: "  OWNER@Example.COM " }, ON);
    const b = await authorizeDevLogin({ email: "second@example.com" }, ON);
    expect(a?.email).toBe("owner@example.com");
    expect(b?.email).toBe("second@example.com");
  });

  it("refuses an address that is not on the list", async () => {
    await expect(
      authorizeDevLogin({ email: "stranger@example.com" }, ON),
    ).resolves.toBeNull();
  });

  it("refuses a near miss rather than matching loosely", async () => {
    for (const email of [
      "owner@example.com.evil.test",
      "xowner@example.com",
      "owner@example.co",
    ]) {
      await expect(authorizeDevLogin({ email }, ON)).resolves.toBeNull();
    }
  });

  it("refuses everything when the gate is off, even an allowlisted address", async () => {
    const off = env({ AUTH_DEV_LOGIN_EMAILS: "owner@example.com" });
    await expect(authorizeDevLogin({ email: "owner@example.com" }, off)).resolves.toBeNull();
  });

  it("refuses when the allowlist is missing, rather than letting everybody in", async () => {
    const noList = env({ AUTH_DEV_LOGIN: "1" });
    await expect(authorizeDevLogin({ email: "anyone@example.com" }, noList)).resolves.toBeNull();
  });

  it("refuses missing, empty and non-string input", async () => {
    await expect(authorizeDevLogin(undefined, ON)).resolves.toBeNull();
    await expect(authorizeDevLogin({}, ON)).resolves.toBeNull();
    await expect(authorizeDevLogin({ email: "" }, ON)).resolves.toBeNull();
    await expect(authorizeDevLogin({ email: "   " }, ON)).resolves.toBeNull();
    await expect(authorizeDevLogin({ email: 42 }, ON)).resolves.toBeNull();
    await expect(authorizeDevLogin({ email: null }, ON)).resolves.toBeNull();
  });
});

describe("deterministicUserId", () => {
  it("is stable for an address, so a session's sub survives a redeploy", async () => {
    const first = await deterministicUserId("owner@example.com");
    const second = await deterministicUserId("owner@example.com");
    expect(first).toBe(second);
  });

  it("differs between addresses", async () => {
    expect(await deterministicUserId("a@example.com")).not.toBe(
      await deterministicUserId("b@example.com"),
    );
  });
});

it("exposes the provider id the action and the callback URL both use", () => {
  expect(DEV_LOGIN_PROVIDER_ID).toBe("dev-login");
});
