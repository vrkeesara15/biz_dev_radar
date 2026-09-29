"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { HealthTab } from "@/components/admin/health-tab";
import { RunHistoryTab } from "@/components/admin/run-history-tab";
import { SourcesTab } from "@/components/admin/sources-tab";
import { TenantsTab } from "@/components/admin/tenants-tab";
import { UsageTab } from "@/components/admin/usage-tab";
import { currentPeriod, isPeriod } from "@/lib/admin/format";
import { cn } from "@/lib/utils";

export const TABS = [
  { id: "sources", label: "Sources" },
  { id: "runs", label: "Run history" },
  { id: "tenants", label: "Tenants" },
  { id: "usage", label: "Usage" },
  { id: "health", label: "Health" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function parseTab(value: string | null): TabId {
  return TABS.some((tab) => tab.id === value) ? (value as TabId) : "sources";
}

/**
 * Admin console (SPEC 10.4 screen 9). Tab, selected source, selected tenant and
 * usage month all live in the URL, so a console view can be linked in a ticket.
 */
export function AdminScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const tab = parseTab(params.get("tab"));
  const source = params.get("source");
  const tenant = params.get("tenant");
  const periodParam = params.get("period");
  const period = periodParam && isPeriod(periodParam) ? periodParam : currentPeriod();

  const setParams = React.useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(changes)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      const search = next.toString();
      router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  return (
    <div className="grid gap-6">
      <header className="grid gap-1">
        <h1 className="font-heading text-xl font-semibold tracking-tight">Admin console</h1>
        <p className="text-sm text-muted-foreground">
          Source health, run history, tenants, usage and LLM cost.
        </p>
      </header>

      {/* M7-13: role="tab" needs a role="tablist" parent, and each tab needs to
          name the panel it controls — without them a screen reader announces
          five loose buttons and a panel with no owner (axe
          `aria-required-parent`). Every tab stays in the natural tab order
          rather than using a roving tabindex, so no tab becomes unreachable. */}
      <nav aria-label="Admin sections" className="border-b">
        <div role="tablist" aria-label="Admin sections" className="flex flex-wrap gap-1">
          {TABS.map((item) => {
            const active = item.id === tab;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`admin-tab-${item.id}`}
                aria-selected={active}
                aria-controls={active ? "admin-tabpanel" : undefined}
                data-testid={`admin-tab-${item.id}`}
                onClick={() => setParams({ tab: item.id })}
                className={cn(
                  "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
                  active
                    ? "border-primary font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {item.label}
              </button>
            );
          })}
        </div>
      </nav>

      <div role="tabpanel" id="admin-tabpanel" aria-labelledby={`admin-tab-${tab}`}>
        {tab === "sources" ? (
          <SourcesTab
            onOpenRuns={(sourceId) => setParams({ tab: "runs", source: sourceId })}
          />
        ) : null}
        {tab === "runs" ? (
          <RunHistoryTab
            sourceId={source}
            onSource={(sourceId) => setParams({ source: sourceId })}
          />
        ) : null}
        {tab === "tenants" ? (
          <TenantsTab
            period={period}
            selected={tenant}
            onSelect={(tenantId) => setParams({ tenant: tenantId })}
          />
        ) : null}
        {tab === "usage" ? (
          <UsageTab period={period} onPeriod={(value) => setParams({ period: value })} />
        ) : null}
        {tab === "health" ? <HealthTab /> : null}
      </div>
    </div>
  );
}
