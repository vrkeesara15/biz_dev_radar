"use client";

import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import {
  disconnectCalendar,
  getMyCalendar,
  rotateCalendarToken,
  type MeCalendar,
} from "@/lib/pursuits/api";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

const PROVIDER_LABELS: Record<string, string> = {
  google_calendar: "Google Calendar",
  microsoft_calendar: "Outlook Calendar",
};

const providerLabel = (provider: string) =>
  PROVIDER_LABELS[provider] ?? provider.replace(/_/g, " ");

/**
 * The iCal feed and the connected calendars (M6-04, OQ-120/OQ-122).
 *
 * The feed URL carries a signed token as its credential, so rotating it
 * revokes every link handed out before — the button says so before it acts.
 */
export function SubscribePanel({ className }: { className?: string }) {
  const [calendar, setCalendar] = React.useState<MeCalendar | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    getMyCalendar(controller.signal)
      .then(setCalendar)
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "The calendar feed could not be read"));
      });
    return () => controller.abort();
  }, []);

  const copy = async () => {
    const url = calendar?.feed_url;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Feed URL copied.");
    } catch {
      toast.error("Copy failed — select the URL and copy it by hand.");
    }
  };

  const rotate = async () => {
    setBusy("rotate");
    try {
      setCalendar(await rotateCalendarToken());
      toast.success("A new feed URL was issued. The old one no longer works.");
    } catch (caught) {
      toast.error(describe(caught, "Could not rotate the feed token"));
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async (connectionId: string, provider: string) => {
    setBusy(connectionId);
    try {
      await disconnectCalendar(connectionId);
      setCalendar((current) =>
        current
          ? { ...current, connections: current.connections.filter((row) => row.id !== connectionId) }
          : current,
      );
      toast.success(`${providerLabel(provider)} disconnected.`);
    } catch (caught) {
      toast.error(describe(caught, "Could not disconnect that calendar"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card data-testid="subscribe-panel" className={className}>
      <CardHeader>
        <CardTitle>Subscribe</CardTitle>
        <CardDescription>
          Your personal feed of every key date you own or have a task on. Treat the URL as a password.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <div className="grid gap-1.5">
          <Label htmlFor="calendar-feed-url">iCal feed URL</Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="calendar-feed-url"
              readOnly
              data-testid="feed-url"
              className="min-w-0 flex-1 font-mono text-xs"
              value={calendar?.feed_url ?? ""}
              placeholder={calendar ? "No feed yet — issue one below" : "Loading…"}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void copy()}
              disabled={!calendar?.feed_url}
            >
              Copy
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => void rotate()} disabled={busy === "rotate"}>
              {calendar?.feed_url ? "Rotate token" : "Issue a feed URL"}
            </Button>
          </div>
          {calendar?.feed_url ? (
            <p className="text-xs text-muted-foreground">
              Rotating issues a new URL and stops every calendar already subscribed to the old one.
            </p>
          ) : null}
        </div>

        <div className="grid gap-2">
          <h3 className="text-sm font-medium">Connected calendars</h3>
          {calendar && calendar.connections.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No calendar is connected. Events are pushed to Google or Outlook once a connection exists; until
              then the feed above is the way in.
            </p>
          ) : null}
          {calendar?.connections.length ? (
            <ul className="grid divide-y" data-testid="calendar-connections">
              {calendar.connections.map((row) => (
                <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{providerLabel(row.provider)}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {row.calendar_id}
                      {row.last_error ? ` · last error: ${row.last_error}` : ""}
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    disabled={busy === row.id}
                    onClick={() => void disconnect(row.id, row.provider)}
                    aria-label={`Disconnect ${providerLabel(row.provider)}`}
                  >
                    Disconnect
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
