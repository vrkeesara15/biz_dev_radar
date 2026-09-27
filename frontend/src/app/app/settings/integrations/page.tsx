import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { IntegrationsScreen } from "@/components/settings/integrations-screen";
import { canManageIntegrations } from "@/lib/settings/roles";

export const metadata: Metadata = { title: "Integrations" };

export default async function IntegrationsSettingsPage() {
  const session = await auth();
  // The API answers 403 anyway; not routing there keeps the tab honest.
  if (!canManageIntegrations(session?.user?.role)) redirect("/app/settings/profile");
  return <IntegrationsScreen />;
}
