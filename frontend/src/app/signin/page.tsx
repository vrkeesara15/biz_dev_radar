import type { Metadata } from "next";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getProviderAvailability } from "@/lib/auth-providers";

import {
  signInWithDevLogin,
  signInWithEmail,
  signInWithGoogle,
  signInWithMicrosoft,
} from "./actions";

export const metadata: Metadata = { title: "Sign in" };
export const dynamic = "force-dynamic";

const ERROR_MESSAGES: Record<string, string> = {
  EmailRequired: "Enter your work email to receive a sign-in link.",
  OAuthAccountNotLinked:
    "This email is already linked to a different sign-in method.",
  AccessDenied: "You do not have access to this workspace.",
  Configuration: "Sign-in is not configured. Contact your administrator.",
  Verification: "That sign-in link has expired or was already used.",
  CredentialsSignin: "That email is not on the demo allowlist.",
};

type SignInPageProps = {
  searchParams?: Promise<{ error?: string }>;
};

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const params = (await searchParams) ?? {};
  const available = getProviderAvailability();
  const errorMessage = params.error
    ? (ERROR_MESSAGES[params.error] ?? "Sign-in failed. Please try again.")
    : null;

  return (
    <main className="flex min-h-dvh items-center justify-center bg-muted/40 px-4 py-12">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <h1 className="font-heading text-lg font-semibold tracking-tight">
            Sign in to BidRadar
          </h1>
          <CardDescription>
            Use your work email or an identity provider.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          {errorMessage ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
            >
              {errorMessage}
            </p>
          ) : null}

          {available.devLogin ? (
            <form action={signInWithDevLogin} className="grid gap-3">
              <div className="flex items-center gap-2">
                <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-400">
                  Demo mode
                </span>
                <span className="text-xs text-muted-foreground">
                  no password, allowlisted addresses only
                </span>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="dev-email">Demo email</Label>
                <Input
                  id="dev-email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@company.com"
                  required
                />
              </div>
              <Button type="submit" className="w-full">
                Sign in (demo)
              </Button>
            </form>
          ) : null}

          <form action={signInWithEmail} className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="email">Work email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                required
                disabled={!available.email}
              />
            </div>
            <Button type="submit" className="w-full" disabled={!available.email}>
              Email me a sign-in link
            </Button>
            {!available.email ? (
              <p className="text-xs text-muted-foreground">
                Email sign-in is not configured (EMAIL_SERVER / EMAIL_FROM).
              </p>
            ) : null}
          </form>

          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            or
            <span className="h-px flex-1 bg-border" />
          </div>

          <div className="grid gap-2">
            <form action={signInWithGoogle}>
              <Button
                type="submit"
                variant="outline"
                className="w-full"
                disabled={!available.google}
              >
                Continue with Google
              </Button>
            </form>
            <form action={signInWithMicrosoft}>
              <Button
                type="submit"
                variant="outline"
                className="w-full"
                disabled={!available.microsoft}
              >
                Continue with Microsoft
              </Button>
            </form>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
