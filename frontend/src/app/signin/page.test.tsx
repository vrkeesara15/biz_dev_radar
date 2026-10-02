import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./actions", () => ({
  signInWithDevLogin: vi.fn(),
  signInWithEmail: vi.fn(),
  signInWithGoogle: vi.fn(),
  signInWithMicrosoft: vi.fn(),
}));

import SignInPage from "./page";

describe("SignInPage", () => {
  it("renders the heading, email form and OAuth buttons", async () => {
    render(await SignInPage({ searchParams: Promise.resolve({}) }));

    expect(
      screen.getByRole("heading", { level: 1, name: "Sign in to BidRadar" }),
    ).toBeTruthy();
    expect(screen.getByLabelText("Work email")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Email me a sign-in link" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Continue with Microsoft" }),
    ).toBeTruthy();
  });

  it("shows a friendly message for a known error code", async () => {
    render(
      await SignInPage({ searchParams: Promise.resolve({ error: "Verification" }) }),
    );
    expect(screen.getByRole("alert").textContent).toMatch(/expired/i);
  });
});

describe("the demo sign-in form", () => {
  afterEach(() => {
    delete process.env.AUTH_DEV_LOGIN;
    delete process.env.AUTH_DEV_LOGIN_EMAILS;
  });

  it("is absent by default — nothing on this page signs anyone in without a provider", async () => {
    render(await SignInPage({ searchParams: Promise.resolve({}) }));
    expect(screen.queryByLabelText("Demo email")).toBeNull();
    expect(screen.queryByText(/demo mode/i)).toBeNull();
  });

  it("is absent when the flag is on but no address is allowlisted", async () => {
    process.env.AUTH_DEV_LOGIN = "1";
    render(await SignInPage({ searchParams: Promise.resolve({}) }));
    expect(screen.queryByLabelText("Demo email")).toBeNull();
  });

  it("appears with a visible demo badge when both variables are set", async () => {
    process.env.AUTH_DEV_LOGIN = "1";
    process.env.AUTH_DEV_LOGIN_EMAILS = "owner@example.com";
    render(await SignInPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByLabelText("Demo email")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in (demo)" })).toBeTruthy();
    expect(screen.getByText(/demo mode/i)).toBeTruthy();
  });

  it("explains a rejected address rather than showing a generic failure", async () => {
    render(
      await SignInPage({
        searchParams: Promise.resolve({ error: "CredentialsSignin" }),
      }),
    );
    expect(screen.getByRole("alert").textContent).toMatch(/allowlist/i);
  });
});
