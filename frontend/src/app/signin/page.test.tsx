import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./actions", () => ({
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
