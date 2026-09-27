"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { Role } from "@/types/next-auth";
import { cn } from "@/lib/utils";

export type SettingsTab = {
  href: string;
  label: string;
  /** SPEC 3: billing, users, integrations and retention are the owner's. */
  ownerOnly?: boolean;
};

export const SETTINGS_TABS: readonly SettingsTab[] = [
  { href: "/app/settings/profile", label: "Profile" },
  { href: "/app/settings/users", label: "Users & roles", ownerOnly: true },
  { href: "/app/settings/notifications", label: "Notifications" },
  { href: "/app/settings/saved-searches", label: "Saved searches" },
  { href: "/app/settings/integrations", label: "Integrations", ownerOnly: true },
  { href: "/app/settings/billing", label: "Billing", ownerOnly: true },
  // Consent and personal data requests are everyone's; the tenant-wide export
  // and erasure inside are the owner's, and the screen hides them.
  { href: "/app/settings/privacy", label: "Data & privacy" },
] as const;

/** Tabs this role may see. */
export function visibleSettingsTabs(role: Role | undefined, tabs: readonly SettingsTab[] = SETTINGS_TABS) {
  return tabs.filter((tab) => !tab.ownerOnly || role === "tenant_owner");
}

export function SettingsNav({
  role,
  tabs = SETTINGS_TABS,
}: {
  role?: Role;
  tabs?: readonly SettingsTab[];
}) {
  const pathname = usePathname();
  const visible = visibleSettingsTabs(role, tabs);
  return (
    <nav aria-label="Settings sections" className="flex flex-wrap gap-1 border-b">
      {visible.map((tab) => {
        const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            data-testid={`settings-tab-${tab.href.split("/").pop()}`}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
              active
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
