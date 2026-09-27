import type { Metadata } from "next";
import { Suspense } from "react";

import { auth } from "@/auth";
import { AdminScreen } from "@/components/admin/admin-screen";

export const metadata: Metadata = { title: "Admin console" };

/**
 * SPEC 3: the console belongs to the platform admin only. The backend enforces
 * this on every route; the page refuses first so a tenant user sees an
 * explanation instead of five failed requests.
 */
export default async function AdminPage() {
  const session = await auth();
  if (session?.user?.role !== "platform_admin") {
    return (
      <section data-testid="admin-forbidden" className="grid max-w-prose gap-2">
        <h1 className="font-heading text-xl font-semibold tracking-tight">Admin console</h1>
        <p className="text-sm text-muted-foreground">
          403 — the admin console is restricted to platform administrators. Tenant settings live
          under Settings; ask us to open a support-access session if you need help with your data.
        </p>
      </section>
    );
  }
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <AdminScreen />
    </Suspense>
  );
}
