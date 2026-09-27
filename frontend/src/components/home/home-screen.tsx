"use client";

import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ScoreBadge } from "@/components/opportunities/badges";
import { DueTime, useNow } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api/browser";
import {
  DASHBOARD_UNAVAILABLE_MESSAGE,
  getDashboard,
  stageLabel,
  type Dashboard,
} from "@/lib/dashboard/api";
import { formatMoneyCompact } from "@/lib/money";
import {
  listNotifications,
  markNotificationRead,
  relativeTime,
  toBellItem,
  type BellItem,
} from "@/lib/notifications/api";
import {
  ApiError,
  NotAvailableError,
  normalizeMatch,
  searchOpportunities,
  type OpportunityItem,
} from "@/lib/opportunities/api";
import { DEFAULT_FILTERS, searchHref } from "@/lib/opportunities/filters";
import { buyerPath } from "@/lib/opportunities/format";

/** SPEC 6: High fit is 70 and above. */
const HIGH_FIT = 70;
/** Still biddable: closed, cancelled and awarded notices are not "today's matches". */
const LIVE_STATUSES = ["open", "closing_soon"] as const;
const LIST_LIMIT = 5;

type Load<T> = { kind: "loading" } | { kind: "ready"; data: T } | { kind: "error"; message: string } | { kind: "unavailable" };

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

function CardMessage({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "error" }) {
  return (
    <p
      role={tone === "error" ? "alert" : undefined}
      className={tone === "error" ? "text-sm text-destructive" : "text-sm text-muted-foreground"}
    >
      {children}
    </p>
  );
}

function HighFitCard({ now }: { now: Date }) {
  const [state, setState] = React.useState<Load<OpportunityItem[]>>({ kind: "loading" });

  React.useEffect(() => {
    const controller = new AbortController();
    searchOpportunities(
      { ...DEFAULT_FILTERS, min_score: HIGH_FIT, status: [...LIVE_STATUSES], page_size: LIST_LIMIT },
      controller.signal,
    )
      .then((page) => setState({ kind: "ready", data: page.items }))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: describe(caught, "High-fit matches could not be read") });
      });
    return () => controller.abort();
  }, []);

  const href = searchHref({ min_score: HIGH_FIT, status: [...LIVE_STATUSES] });

  return (
    <Card data-testid="high-fit-card">
      <CardHeader>
        <CardTitle>
          <Link href={href} className="underline-offset-4 hover:underline">
            High-fit today
          </Link>
        </CardTitle>
        <CardDescription>Open notices scoring {HIGH_FIT} or above against your profile.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {state.kind === "loading" ? <CardMessage>Loading…</CardMessage> : null}
        {state.kind === "error" ? <CardMessage tone="error">{state.message}</CardMessage> : null}
        {state.kind === "ready" && !state.data.length ? (
          <CardMessage>
            Nothing at {HIGH_FIT} or above right now. Matches appear here as scoring runs.
          </CardMessage>
        ) : null}
        {state.kind === "ready" && state.data.length ? (
          <ul className="grid divide-y" data-testid="high-fit-list">
            {state.data.map((item) => {
              const match = normalizeMatch(item.match);
              const buyer = buyerPath(item)[0] ?? null;
              return (
                <li key={item.id} className="flex items-start justify-between gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <Link
                      href={`/app/opportunities/${item.id}`}
                      className="line-clamp-2 text-sm font-medium underline-offset-4 hover:underline"
                    >
                      {item.title}
                    </Link>
                    <p className="truncate text-xs text-muted-foreground">
                      {buyer ?? "—"}
                      {item.response_due_at ? (
                        <>
                          {" · "}
                          <DueTime value={item.response_due_at} sourceTz={item.source_tz} now={now} />
                        </>
                      ) : null}
                    </p>
                  </div>
                  <ScoreBadge score={match?.score ?? null} />
                </li>
              );
            })}
          </ul>
        ) : null}
        <div>
          <Link href={href} className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            Open search →
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function DueThisWeekCard({ dashboard, now }: { dashboard: Load<Dashboard | null>; now: Date }) {
  const items = dashboard.kind === "ready" && dashboard.data ? dashboard.data.dueNext7Days : [];
  return (
    <Card data-testid="due-week-card">
      <CardHeader>
        <CardTitle>Due this week</CardTitle>
        <CardDescription>Key dates on tracked pursuits in the next seven days.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {dashboard.kind === "loading" ? <CardMessage>Loading…</CardMessage> : null}
        {dashboard.kind === "unavailable" ? <CardMessage>{DASHBOARD_UNAVAILABLE_MESSAGE}.</CardMessage> : null}
        {dashboard.kind === "error" ? <CardMessage tone="error">{dashboard.message}</CardMessage> : null}
        {dashboard.kind === "ready" && !items.length ? (
          <CardMessage>Nothing due in the next seven days.</CardMessage>
        ) : null}
        {items.length ? (
          <ul className="grid divide-y" data-testid="due-week-list">
            {items.map((item) => {
              const href = item.pursuit_id
                ? `/app/pursuits/${item.pursuit_id}`
                : item.opportunity_id
                  ? `/app/opportunities/${item.opportunity_id}`
                  : null;
              return (
                <li key={item.id} className="flex items-start justify-between gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    {href ? (
                      <Link href={href} className="line-clamp-2 text-sm font-medium underline-offset-4 hover:underline">
                        {item.title}
                      </Link>
                    ) : (
                      <span className="line-clamp-2 text-sm font-medium">{item.title}</span>
                    )}
                    <p className="truncate text-xs text-muted-foreground">
                      {[item.kind, item.buyer].filter(Boolean).join(" · ") || "—"}
                    </p>
                  </div>
                  {item.due_at ? <DueTime value={item.due_at} now={now} showCountdown /> : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PipelineValueCard({ dashboard }: { dashboard: Load<Dashboard | null> }) {
  const data = dashboard.kind === "ready" ? dashboard.data : null;
  const rows = data?.pipelineValueByStage ?? [];
  const total = data?.pipelineTotal ?? { USD: null, INR: null };
  const hasTotal = total.USD !== null || total.INR !== null;

  return (
    <Card data-testid="pipeline-value-card">
      <CardHeader>
        <CardTitle>Pipeline value</CardTitle>
        <CardDescription>Estimated value of active pursuits, by stage.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {dashboard.kind === "loading" ? <CardMessage>Loading…</CardMessage> : null}
        {dashboard.kind === "unavailable" ? <CardMessage>{DASHBOARD_UNAVAILABLE_MESSAGE}.</CardMessage> : null}
        {dashboard.kind === "error" ? <CardMessage tone="error">{dashboard.message}</CardMessage> : null}
        {dashboard.kind === "ready" && !hasTotal ? (
          <CardMessage>No pursuits carry a value yet.</CardMessage>
        ) : null}
        {hasTotal ? (
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1" data-testid="pipeline-total">
            <span className="text-2xl font-semibold tabular-nums">
              {formatMoneyCompact(total.USD, "USD")}
            </span>
            <span className="text-lg tabular-nums text-muted-foreground">
              {formatMoneyCompact(total.INR, "INR")}
            </span>
          </div>
        ) : null}
        {rows.length ? (
          <ul className="grid gap-1 text-sm" data-testid="pipeline-by-stage">
            {rows.map((row) => (
              <li key={row.stage} className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{stageLabel(row.stage)}</span>
                <span className="tabular-nums">
                  {formatMoneyCompact(row.value.USD, "USD")}
                  {row.value.INR !== null ? (
                    <span className="text-muted-foreground"> · {formatMoneyCompact(row.value.INR, "INR")}</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {data?.openByStage.length ? (
          <p className="text-xs text-muted-foreground">
            {data.openByStage.map((entry) => `${stageLabel(entry.stage)} ${entry.count}`).join(" · ")}
          </p>
        ) : null}
        {data && (data.winRate !== null || data.alertPrecision !== null || data.avgHoursSavedPerPackage !== null) ? (
          <dl className="grid grid-cols-3 gap-2 border-t pt-2 text-xs">
            <div>
              <dt className="text-muted-foreground">Win rate</dt>
              <dd className="tabular-nums">{data.winRate !== null ? `${Math.round(data.winRate * 100)}%` : "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Alert precision</dt>
              <dd className="tabular-nums">
                {data.alertPrecision !== null ? `${Math.round(data.alertPrecision * 100)}%` : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Hours saved</dt>
              <dd className="tabular-nums">
                {data.avgHoursSavedPerPackage !== null ? data.avgHoursSavedPerPackage.toFixed(1) : "—"}
              </dd>
            </div>
          </dl>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AlertsInboxCard({ now }: { now: Date }) {
  const [state, setState] = React.useState<Load<BellItem[]>>({ kind: "loading" });
  const [busy, setBusy] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    listNotifications({ unread: true, limit: LIST_LIMIT }, controller.signal)
      .then((page) => setState({ kind: "ready", data: page.items.map(toBellItem) }))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: describe(caught, "The alerts inbox could not be read") });
      });
    return () => controller.abort();
  }, []);

  const markRead = async (item: BellItem) => {
    setBusy(item.id);
    try {
      await markNotificationRead(item.id);
      setState((current) =>
        current.kind === "ready"
          ? { kind: "ready", data: current.data.filter((row) => row.id !== item.id) }
          : current,
      );
    } catch (caught) {
      toast.error(describe(caught, "Could not mark it read"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card data-testid="alerts-inbox-card" className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Alerts inbox</CardTitle>
        <CardDescription>Unread notifications, newest first.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {state.kind === "loading" ? <CardMessage>Loading…</CardMessage> : null}
        {state.kind === "error" ? <CardMessage tone="error">{state.message}</CardMessage> : null}
        {state.kind === "ready" && !state.data.length ? (
          <CardMessage>Inbox zero. New alerts arrive here and in the bell.</CardMessage>
        ) : null}
        {state.kind === "ready" && state.data.length ? (
          <ul className="grid divide-y" data-testid="alerts-inbox-list">
            {state.data.map((item) => (
              <li key={item.id} className="flex flex-wrap items-start justify-between gap-2 py-2 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  {item.href ? (
                    <Link href={item.href} className="text-sm font-medium underline-offset-4 hover:underline">
                      {item.title}
                    </Link>
                  ) : (
                    <span className="text-sm font-medium">{item.title}</span>
                  )}
                  <p className="truncate text-xs text-muted-foreground">
                    {[item.detail, relativeTime(item.createdAt, now)].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {item.score !== null ? <Badge variant="outline">{Math.round(item.score)}</Badge> : null}
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={busy === item.id}
                    onClick={() => void markRead(item)}
                    aria-label={`Mark “${item.title}” read`}
                  >
                    Mark read
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
        <div>
          <Link
            href="/app/settings/notifications"
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Notification settings →
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

/** Home (SPEC 10.4 screen 2). */
export function HomeScreen() {
  const now = useNow();
  const [dashboard, setDashboard] = React.useState<Load<Dashboard | null>>({ kind: "loading" });

  React.useEffect(() => {
    const controller = new AbortController();
    getDashboard(controller.signal)
      .then((data) => setDashboard({ kind: "ready", data }))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        if (caught instanceof NotAvailableError) {
          setDashboard({ kind: "unavailable" });
          return;
        }
        setDashboard({ kind: "error", message: describe(caught, "The dashboard could not be read") });
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Home</h1>
        <p className="text-sm text-muted-foreground">
          Today&apos;s matches, deadlines and pipeline at a glance.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <HighFitCard now={now} />
        <DueThisWeekCard dashboard={dashboard} now={now} />
        <PipelineValueCard dashboard={dashboard} />
        <AlertsInboxCard now={now} />
      </div>
    </div>
  );
}
