import { redirect } from "next/navigation";

import { auth, signOut } from "@/auth";
import { AppNav } from "@/components/app-nav";
import { NotificationBell } from "@/components/notifications/notification-bell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getRegion } from "@/lib/region";

export default async function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();
  if (!session?.user) redirect("/signin");

  const region = getRegion();

  async function handleSignOut() {
    "use server";
    await signOut({ redirectTo: "/signin" });
  }

  return (
    <div className="grid min-h-dvh grid-cols-[13.5rem_1fr]">
      <aside className="flex flex-col border-r bg-sidebar text-sidebar-foreground">
        <div className="flex h-14 items-center px-5 text-sm font-semibold tracking-tight">
          BidRadar
        </div>
        <AppNav role={session.user.role} />
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex h-14 items-center justify-between gap-4 border-b px-6">
          <Badge variant="outline" aria-label={`Region ${region}`}>
            {region}
          </Badge>
          <div className="flex items-center gap-3">
            <NotificationBell />
            <span className="text-sm text-muted-foreground">
              {session.user.email}
            </span>
            <form action={handleSignOut}>
              <Button type="submit" variant="ghost" size="sm">
                Sign out
              </Button>
            </form>
          </div>
        </header>
        <main className="flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
