import type { Metadata } from "next";
import { Suspense } from "react";

import { auth } from "@/auth";
import { PursuitWorkspace } from "@/components/pursuits/workspace/pursuit-workspace";

export const metadata: Metadata = { title: "Pursuit workspace" };

/**
 * SPEC 10.4 screen 6. The role comes from the session on the server so the
 * workspace never draws a control the user's role cannot use (SPEC 3); the
 * API is still the authority on every one of them.
 */
export default async function PursuitPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const session = await auth();
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading the pursuit…</p>}>
      <PursuitWorkspace pursuitId={id} role={session?.user?.role} />
    </Suspense>
  );
}
