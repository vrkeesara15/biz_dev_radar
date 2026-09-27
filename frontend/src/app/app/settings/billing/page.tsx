import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { BillingScreen } from "@/components/settings/billing-screen";
import { canManageBilling } from "@/lib/settings/roles";

export const metadata: Metadata = { title: "Billing" };

export default async function BillingSettingsPage() {
  const session = await auth();
  if (!canManageBilling(session?.user?.role)) redirect("/app/settings/profile");
  return <BillingScreen />;
}
