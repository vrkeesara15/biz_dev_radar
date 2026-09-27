import type { Metadata } from "next";

import { auth } from "@/auth";
import { UsersScreen } from "@/components/settings/users-screen";

export const metadata: Metadata = { title: "Users and roles" };

export default async function UsersSettingsPage() {
  const session = await auth();
  return <UsersScreen role={session?.user?.role} currentUserId={session?.user?.id} />;
}
