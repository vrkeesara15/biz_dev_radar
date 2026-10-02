"use server";

import { AuthError } from "next-auth";
import { redirect } from "next/navigation";

import { signIn } from "@/auth";
import { DEV_LOGIN_PROVIDER_ID } from "@/lib/auth-dev-login";

export async function signInWithEmail(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  if (!email) redirect("/signin?error=EmailRequired");
  try {
    await signIn("nodemailer", { email, redirectTo: "/app" });
  } catch (error) {
    if (error instanceof AuthError) redirect(`/signin?error=${error.type}`);
    throw error;
  }
}

/**
 * Demo-only (AUTH_DEV_LOGIN=1 + AUTH_DEV_LOGIN_EMAILS). The provider is not even
 * registered otherwise, so this redirects to the sign-in page with an error rather
 * than letting anyone in.
 */
export async function signInWithDevLogin(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  if (!email) redirect("/signin?error=EmailRequired");
  try {
    await signIn(DEV_LOGIN_PROVIDER_ID, { email, redirectTo: "/app" });
  } catch (error) {
    if (error instanceof AuthError) redirect(`/signin?error=${error.type}`);
    throw error;
  }
}

export async function signInWithGoogle() {
  await signIn("google", { redirectTo: "/app" });
}

export async function signInWithMicrosoft() {
  await signIn("microsoft-entra-id", { redirectTo: "/app" });
}
