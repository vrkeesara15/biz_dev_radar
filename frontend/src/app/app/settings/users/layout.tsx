import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { canManageMembers } from "@/lib/settings/roles";

/** SPEC 3: users are the tenant owner's; other roles never see the page. */
export default async function UsersLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();
  if (!canManageMembers(session?.user?.role)) redirect("/app/settings/profile");
  return <>{children}</>;
}
