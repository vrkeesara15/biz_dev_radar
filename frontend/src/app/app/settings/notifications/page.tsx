import type { Metadata } from "next";

import { NotificationPrefsForm } from "@/components/notifications/prefs-form";

export const metadata: Metadata = { title: "Notification settings" };

export default function NotificationSettingsPage() {
  return <NotificationPrefsForm />;
}
