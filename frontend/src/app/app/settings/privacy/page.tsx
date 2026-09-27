import type { Metadata } from "next";

import { auth } from "@/auth";
import { PrivacyScreen } from "@/components/settings/privacy-screen";

export const metadata: Metadata = { title: "Data and privacy" };

export default async function PrivacySettingsPage() {
  const session = await auth();
  return <PrivacyScreen role={session?.user?.role} />;
}
