"use client";

import * as React from "react";

import { EmptyNote, ErrorNote, StatusBadge, describeError } from "@/components/admin/common";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getSystemHealth, type SystemHealth } from "@/lib/admin/api";
import { formatTimestamp, relativeTime } from "@/lib/admin/format";

const CHECK_TITLES: Record<string, string> = {
  database: "Database",
  broker: "Celery broker",
  storage: "Object storage",
  adapters: "Source adapters",
};

/** System health cards: DB, broker, storage, adapters (SPEC 3 "view system health"). */
export function HealthTab() {
  const [health, setHealth] = React.useState<SystemHealth | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getSystemHealth(controller.signal)
      .then((result) => setHealth(result))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(err, "Could not load system health."));
      });
    return () => controller.abort();
  }, []);

  if (error) return <ErrorNote message={error} />;
  if (!health) return <EmptyNote>Checking…</EmptyNote>;

  const unhealthy = health.adapters.filter((a) => a.health_status !== "ok");

  return (
    <div className="grid gap-4">
      <p className="flex items-center gap-2 text-sm" data-testid="health-summary">
        <span className="text-muted-foreground">Overall</span>
        <StatusBadge status={health.status} />
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {health.checks.map((check) => (
          <Card key={check.name} data-testid="health-card" data-check={check.name}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-2 text-sm">
                {CHECK_TITLES[check.name] ?? check.name}
                <StatusBadge status={check.status} />
              </CardTitle>
              {check.detail ? <CardDescription>{check.detail}</CardDescription> : null}
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">
              <dl className="grid gap-0.5">
                {Object.entries(check.meta ?? {}).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-2">
                    <dt>{key.replace(/_/g, " ")}</dt>
                    <dd className="truncate font-mono">
                      {typeof value === "object" ? JSON.stringify(value) : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
        ))}
      </div>

      <section aria-label="Adapters needing attention" className="grid gap-2">
        <h3 className="text-sm font-medium">Adapters needing attention</h3>
        {unhealthy.length ? (
          <ul className="grid gap-2">
            {unhealthy.map((adapter) => (
              <li
                key={adapter.source_id}
                data-testid="unhealthy-adapter"
                className="flex flex-wrap items-center gap-3 rounded-xl border p-3 text-sm"
              >
                <span className="font-medium">{adapter.source_id}</span>
                <StatusBadge status={adapter.health_status} />
                <span className="text-muted-foreground">
                  {adapter.consecutive_failures} consecutive failure
                  {adapter.consecutive_failures === 1 ? "" : "s"}
                </span>
                <span className="text-muted-foreground" title={formatTimestamp(adapter.last_run_at)}>
                  last run {relativeTime(adapter.last_run_at)}
                </span>
                {adapter.health_message ? (
                  <span className="basis-full text-xs text-muted-foreground">
                    {adapter.health_message}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Every adapter reports ok.</p>
        )}
      </section>
    </div>
  );
}
