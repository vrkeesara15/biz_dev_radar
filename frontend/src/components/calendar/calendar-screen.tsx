"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { SubscribePanel } from "@/components/calendar/subscribe-panel";
import { useNow, useUserTimeZone } from "@/components/opportunities/due-time";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api/browser";
import {
  CALENDAR_MODES,
  WEEKDAY_LABELS,
  buildGrid,
  civilKey,
  dayKey,
  groupByDay,
  parseCivil,
  shiftAnchor,
  todayIn,
  type CalendarMode,
  type CivilDate,
} from "@/lib/calendar/grid";
import { ApiError } from "@/lib/opportunities/api";
import { dualTz } from "@/lib/opportunities/dates";
import { DEFAULT_PIPELINE_FILTERS } from "@/lib/pursuits/filters";
import { loadCalendarEvents, type CalendarEvent } from "@/lib/pursuits/api";
import { keyDateKindLabel } from "@/lib/pursuits/key-dates";
import { cn } from "@/lib/utils";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

const isMode = (value: string): value is CalendarMode =>
  (CALENDAR_MODES as readonly string[]).includes(value);

function EventChip({
  event,
  userTz,
  compact,
}: {
  event: CalendarEvent;
  userTz: string | null;
  compact: boolean;
}) {
  const dual = dualTz(event.at, event.at.buyer_tz, userTz);
  const when = dual?.display ?? event.at.display;
  const kind = keyDateKindLabel(event.kind);
  return (
    <li>
      <Link
        href={`/app/pursuits/${event.pursuitId}`}
        data-testid="calendar-event"
        data-kind={event.kind}
        title={`${kind} — ${event.pursuitTitle} — ${when}`}
        className={cn(
          "block rounded-md border-l-2 border-primary/60 bg-primary/5 px-1.5 py-1 text-left text-[11px] leading-tight underline-offset-4 hover:bg-primary/10 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
          event.acknowledgedAt && "border-muted-foreground/40 bg-muted/60",
        )}
      >
        <span className="block font-medium">{kind}</span>
        <span className="block truncate text-muted-foreground">{event.pursuitTitle}</span>
        <span className={cn("block text-muted-foreground tabular-nums", compact && "truncate")}>{when}</span>
      </Link>
    </li>
  );
}

/** SPEC 10.4 screen 7: every key date across the tenant, in month or week view. */
export function CalendarScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const now = useNow();
  const userTz = useUserTimeZone();
  const tz = userTz ?? "UTC";

  const modeParam = (params.get("mode") ?? "").toLowerCase();
  const mode: CalendarMode = isMode(modeParam) ? modeParam : "month";
  const today = React.useMemo(() => todayIn(tz, now), [tz, now]);
  const anchor: CivilDate = parseCivil(params.get("date")) ?? today;

  const [events, setEvents] = React.useState<CalendarEvent[] | null>(null);
  const [capped, setCapped] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    loadCalendarEvents(DEFAULT_PIPELINE_FILTERS, controller.signal)
      .then((load) => {
        setEvents(load.events);
        setCapped(load.capped);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "The calendar could not be read"));
      });
    return () => controller.abort();
  }, []);

  const navigate = React.useCallback(
    (nextMode: CalendarMode, nextAnchor: CivilDate) => {
      const search = new URLSearchParams();
      if (nextMode !== "month") search.set("mode", nextMode);
      if (civilKey(nextAnchor) !== civilKey(today)) search.set("date", civilKey(nextAnchor));
      const query = search.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, today],
  );

  const grid = React.useMemo(() => buildGrid(mode, anchor, { today }), [mode, anchor, today]);
  const buckets = React.useMemo(
    () => groupByDay(events ?? [], tz, (event) => event.at.utc),
    [events, tz],
  );
  const visible = React.useMemo(() => {
    const keys = new Set(grid.days.map((day) => day.key));
    return (events ?? []).filter((event) => keys.has(dayKey(event.at.utc, tz)));
  }, [events, grid, tz]);

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Calendar</h1>
        <p className="text-sm text-muted-foreground">
          Every key date across your pursuits, in the buyer&apos;s clock and yours.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-label={mode === "week" ? "Previous week" : "Previous month"}
            onClick={() => navigate(mode, shiftAnchor(mode, anchor, -1))}
          >
            ←
          </Button>
          <h2 data-testid="calendar-title" className="min-w-48 text-center text-base font-medium">
            {grid.title}
          </h2>
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-label={mode === "week" ? "Next week" : "Next month"}
            onClick={() => navigate(mode, shiftAnchor(mode, anchor, 1))}
          >
            →
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => navigate(mode, today)}>
            Today
          </Button>
        </div>
        <div role="group" aria-label="Calendar view" className="inline-flex rounded-lg border p-0.5">
          {CALENDAR_MODES.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={mode === option}
              data-testid={`calendar-${option}`}
              onClick={() => navigate(option, anchor)}
              className={cn(
                "rounded-md px-3 py-1 text-sm capitalize transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                mode === option ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <p role="alert" className="rounded-lg border px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {!events && !error ? (
        <p role="status" className="rounded-lg border px-3 py-2 text-sm text-muted-foreground">
          Loading key dates…
        </p>
      ) : null}
      {capped ? (
        <p role="status" className="rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
          Only the first 200 pursuits are shown. Narrow the pipeline filters to see the rest.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table
          data-testid="calendar-grid"
          data-mode={mode}
          className="w-full min-w-3xl table-fixed border-collapse"
        >
          <caption className="sr-only">{`${grid.title} — ${visible.length} key dates`}</caption>
          <thead>
            <tr>
              {WEEKDAY_LABELS.map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="border-b px-2 pb-1 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.weeks.map((week) => (
              <tr key={week[0].key}>
                {week.map((day) => {
                  const dayEvents = buckets.get(day.key) ?? [];
                  return (
                    <td
                      key={day.key}
                      data-testid="calendar-day"
                      data-date={day.key}
                      className={cn(
                        "h-28 w-1/7 border p-1 align-top",
                        mode === "week" && "h-64",
                        !day.inMonth && "bg-muted/40 text-muted-foreground",
                        day.isToday && "ring-2 ring-inset ring-primary/50",
                      )}
                    >
                      <div className="mb-1 flex items-center justify-between">
                        <span className={cn("text-xs tabular-nums", day.isToday && "font-semibold text-primary")}>
                          {day.day}
                        </span>
                        {day.isToday ? <span className="sr-only">Today</span> : null}
                      </div>
                      <ul className="grid gap-1">
                        {dayEvents.map((event) => (
                          <EventChip key={event.id} event={event} userTz={userTz} compact={mode === "month"} />
                        ))}
                      </ul>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {events && visible.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Nothing on this {mode}</CardTitle>
            <CardDescription>
              Key dates appear here as soon as a pursuit carries one. Open a pursuit to add your own.
            </CardDescription>
          </CardHeader>
          <CardContent />
        </Card>
      ) : null}

      <SubscribePanel />
    </div>
  );
}
