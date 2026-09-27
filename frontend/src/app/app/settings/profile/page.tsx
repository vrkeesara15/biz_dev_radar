import type { Metadata } from "next";

import { ProfileTab } from "@/components/settings/profile-tab";

export const metadata: Metadata = { title: "Profile settings" };

export default function ProfileSettingsPage() {
  return <ProfileTab />;
}
