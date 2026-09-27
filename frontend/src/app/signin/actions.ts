"use server";

import { AuthError } from "next-auth";
import { redirect } from "next/navigation";

import { signIn } from "@/auth";

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

export async function signInWithGoogle() {
  await signIn("google", { redirectTo: "/app" });
}

export async function signInWithMicrosoft() {
  await signIn("microsoft-entra-id", { redirectTo: "/app" });
}
