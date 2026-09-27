import { auth } from "@/auth";
import { SettingsNav } from "@/components/settings/settings-nav";

/** Settings (SPEC 10.4 screen 8). Tabs are routes, so a section can be linked. */
export default async function SettingsLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Your profile, notifications and the tenant&apos;s configuration.
        </p>
      </div>
      <SettingsNav role={session?.user?.role} />
      <div>{children}</div>
    </div>
  );
}
