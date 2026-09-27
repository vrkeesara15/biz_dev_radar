"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Role } from "@/types/next-auth";
import { cn } from "@/lib/utils";

export const NAV_ITEMS = [
  { href: "/app", label: "Home" },
  { href: "/app/onboarding", label: "Profile setup" },
  { href: "/app/opportunities", label: "Opportunities" },
  { href: "/app/pipeline", label: "Pipeline" },
  { href: "/app/calendar", label: "Calendar" },
  { href: "/app/settings", label: "Settings" },
  { href: "/app/admin", label: "Admin" },
] as const;

/** SPEC 3: the admin console is the platform admin's, so nobody else is shown the door. */
export function visibleNavItems(role: Role | undefined) {
  return NAV_ITEMS.filter(
    (item) => item.href !== "/app/admin" || role === "platform_admin",
  );
}

export function AppNav({ role }: { role?: Role }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary" className="grid gap-0.5 p-2">
      {visibleNavItems(role).map((item) => {
        const active =
          item.href === "/app"
            ? pathname === "/app"
            : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
              active && "bg-muted font-medium text-foreground",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
