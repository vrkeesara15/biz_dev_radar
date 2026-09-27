import type { Metadata } from "next";
import Link from "next/link";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";

export const metadata: Metadata = { title: "Check your email" };

export default function CheckEmailPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-muted/40 px-4 py-12">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <h1 className="font-heading text-lg font-semibold tracking-tight">
            Check your email
          </h1>
          <CardDescription>
            A sign-in link has been sent to your address. It expires in 24 hours.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Wrong address?{" "}
          <Link href="/signin" className="text-foreground underline underline-offset-4">
            Back to sign in
          </Link>
        </CardContent>
      </Card>
    </main>
  );
}
