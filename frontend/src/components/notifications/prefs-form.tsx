"use client";

import * as React from "react";
import { toast } from "sonner";

import { PushOptIn } from "@/components/notifications/push-opt-in";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { errorMessage } from "@/lib/api/browser";
import { getNotificationPrefs, putNotificationPrefs } from "@/lib/notifications/api";
import {
  CHANNELS,
  CHANNEL_LABELS,
  EVENTS,
  EVENT_META,
  categoryLabel,
  normalizePrefs,
  prefsReducer,
  samePrefs,
  toPrefsBody,
  validatePrefs,
  type Channel,
  type EventType,
  type PrefsState,
} from "@/lib/notifications/prefs";
import { ApiError } from "@/lib/opportunities/api";
import { browserTimeZone, filterTimeZones, timeZoneLabel, timeZoneOffset } from "@/lib/timezones";

const TZ_OPTION_LIMIT = 80;

function TimeZonePicker({
  value,
  onChange,
  invalid,
}: {
  value: string;
  onChange: (zone: string) => void;
  invalid?: boolean;
}) {
  const [query, setQuery] = React.useState("");
  const options = React.useMemo(() => {
    const matches = filterTimeZones(query, undefined, TZ_OPTION_LIMIT);
    return matches.includes(value) ? matches : [value, ...matches];
  }, [query, value]);
  const offset = timeZoneOffset(value);

  return (
    <div className="grid gap-1.5">
      <Label htmlFor="tz-search">Search time zones</Label>
      <Input
        id="tz-search"
        type="search"
        value={query}
        placeholder="kolkata, eastern, IST…"
        onChange={(event) => setQuery(event.target.value)}
        aria-describedby="tz-help"
      />
      <Label htmlFor="tz">Time zone</Label>
      <NativeSelect
        id="tz"
        aria-invalid={invalid || undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((zone) => (
          <option key={zone} value={zone}>
            {timeZoneLabel(zone)}
          </option>
        ))}
      </NativeSelect>
      <p id="tz-help" className="text-xs text-muted-foreground">
        {options.length >= TZ_OPTION_LIMIT
          ? `Showing the first ${TZ_OPTION_LIMIT} matches — keep typing to narrow them.`
          : `Quiet hours, the digest and every date in an email use this zone${offset ? ` (UTC${offset})` : ""}.`}
      </p>
    </div>
  );
}

function ChannelMatrix({
  state,
  onToggle,
  onColumn,
}: {
  state: PrefsState;
  onToggle: (event: EventType, channel: Channel) => void;
  onColumn: (channel: Channel, on: boolean) => void;
}) {
  const columnState = (channel: Channel) => {
    const on = EVENTS.filter((event) => (state.channelsByEvent[event] ?? []).includes(channel)).length;
    return on === EVENTS.length ? "all" : on === 0 ? "none" : "some";
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-xl border-separate border-spacing-0 text-sm" data-testid="channel-matrix">
        <caption className="sr-only">Channels for each notification event</caption>
        <thead>
          <tr>
            <th scope="col" className="w-2/5 border-b px-2 py-2 text-left font-medium">
              Event
            </th>
            {CHANNELS.map((channel) => (
              <th key={channel} scope="col" className="border-b px-2 py-2 text-center font-medium">
                <span className="block">{CHANNEL_LABELS[channel]}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="mt-0.5 font-normal text-muted-foreground"
                  onClick={() => onColumn(channel, columnState(channel) !== "all")}
                  aria-label={`${columnState(channel) === "all" ? "Clear" : "Select"} ${CHANNEL_LABELS[channel]} for every event`}
                >
                  {columnState(channel) === "all" ? "Clear" : "All"}
                </Button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {EVENT_META.map((event) => {
            const chosen = state.channelsByEvent[event.value] ?? [];
            return (
              <tr key={event.value} data-testid={`event-row-${event.value}`}>
                <th scope="row" className="border-b px-2 py-2 text-left font-normal align-top">
                  <span className="block font-medium">{event.label}</span>
                  <span className="block text-xs text-muted-foreground">{event.description}</span>
                  {chosen.length === 0 ? (
                    <Badge variant="outline" className="mt-1">
                      Silenced
                    </Badge>
                  ) : null}
                </th>
                {CHANNELS.map((channel) => {
                  const id = `pref-${event.value}-${channel}`;
                  return (
                    <td key={channel} className="border-b px-2 py-2 text-center align-middle">
                      <Checkbox
                        id={id}
                        checked={chosen.includes(channel)}
                        onChange={() => onToggle(event.value, channel)}
                        aria-label={`${CHANNEL_LABELS[channel]} for ${event.label}`}
                      />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Settings > Notifications (SPEC 7 "per-user settings", 10.4 screen 8). */
export function NotificationPrefsForm() {
  const [state, setState] = React.useState<PrefsState | null>(null);
  const [saved, setSaved] = React.useState<PrefsState | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    const controller = new AbortController();
    getNotificationPrefs(controller.signal)
      .then((wire) => {
        const next = normalizePrefs(wire, browserTimeZone());
        setState(next);
        setSaved(next);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(
          caught instanceof ApiError
            ? errorMessage(caught.body, `Preferences could not be read (${caught.status}).`)
            : "Preferences could not be read.",
        );
      });
    return () => controller.abort();
  }, []);

  const errors = state ? validatePrefs(state) : {};
  const dirty = state && saved ? !samePrefs(state, saved) : false;
  const dispatch = (action: Parameters<typeof prefsReducer>[1]) =>
    setState((current) => (current ? prefsReducer(current, action) : current));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!state || Object.keys(errors).length) return;
    setSaving(true);
    try {
      const wire = await putNotificationPrefs(toPrefsBody(state));
      const next = normalizePrefs({ ...wire, unsubscribed_categories: state.unsubscribedCategories });
      setState(next);
      setSaved(next);
      toast.success("Notification preferences saved");
    } catch (caught) {
      toast.error(
        caught instanceof ApiError
          ? errorMessage(caught.body, `Could not save (${caught.status})`)
          : "Could not save your preferences",
      );
    } finally {
      setSaving(false);
    }
  };

  if (loadError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {loadError}
      </p>
    );
  }
  if (!state) return <p className="text-sm text-muted-foreground">Loading your preferences…</p>;

  const quietOn = state.quietHoursStart !== null || state.quietHoursEnd !== null;

  return (
    <form className="grid gap-6" onSubmit={submit} data-testid="notification-prefs-form">
      <Card>
        <CardHeader>
          <CardTitle>Channels per event</CardTitle>
          <CardDescription>
            Where each kind of notification reaches you. The in-app bell always keeps a copy.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ChannelMatrix
            state={state}
            onToggle={(event, channel) => dispatch({ type: "toggle", event, channel })}
            onColumn={(channel, on) => dispatch({ type: "setColumn", channel, on })}
          />
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Timing</CardTitle>
            <CardDescription>
              Instant alerts are held during quiet hours unless the deadline is under 72 hours.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <TimeZonePicker
              value={state.tz}
              invalid={!!errors.tz}
              onChange={(tz) => dispatch({ type: "tz", tz })}
            />

            <fieldset className="grid gap-1.5">
              <legend className="text-sm font-medium">Quiet hours</legend>
              <div className="flex flex-wrap items-end gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="quiet-start">Start</Label>
                  <Input
                    id="quiet-start"
                    type="time"
                    className="w-32"
                    value={state.quietHoursStart ?? ""}
                    aria-invalid={!!errors.quiet_hours || undefined}
                    onChange={(event) =>
                      dispatch({
                        type: "quietHours",
                        start: event.target.value || null,
                        end: state.quietHoursEnd,
                      })
                    }
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="quiet-end">End</Label>
                  <Input
                    id="quiet-end"
                    type="time"
                    className="w-32"
                    value={state.quietHoursEnd ?? ""}
                    aria-invalid={!!errors.quiet_hours || undefined}
                    onChange={(event) =>
                      dispatch({
                        type: "quietHours",
                        start: state.quietHoursStart,
                        end: event.target.value || null,
                      })
                    }
                  />
                </div>
                {quietOn ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => dispatch({ type: "quietHours", start: null, end: null })}
                  >
                    Clear
                  </Button>
                ) : null}
              </div>
              {errors.quiet_hours ? (
                <p role="alert" className="text-xs text-destructive">
                  {errors.quiet_hours}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Leave both empty to be reachable around the clock.
                </p>
              )}
            </fieldset>

            <div className="grid gap-1.5">
              <Label htmlFor="digest-time">Digest time</Label>
              <Input
                id="digest-time"
                type="time"
                className="w-32"
                value={state.digestTime}
                aria-invalid={!!errors.digest_time || undefined}
                onChange={(event) => dispatch({ type: "digestTime", time: event.target.value })}
              />
              {errors.digest_time ? (
                <p role="alert" className="text-xs text-destructive">
                  {errors.digest_time}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Daily roll-up of Medium matches, in your time zone.
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Thresholds</CardTitle>
            <CardDescription>
              SPEC 6 bands: High ≥ 70 alerts instantly, Medium 50–69 waits for the digest.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="min-score-instant">Minimum score for an instant alert</Label>
              <Input
                id="min-score-instant"
                type="number"
                min={0}
                max={100}
                className="w-28"
                value={state.minScoreInstant}
                aria-invalid={!!errors.min_score_instant || undefined}
                onChange={(event) =>
                  dispatch({ type: "minScore", which: "instant", value: Number(event.target.value) })
                }
              />
              {errors.min_score_instant ? (
                <p role="alert" className="text-xs text-destructive">
                  {errors.min_score_instant}
                </p>
              ) : null}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="min-score-digest">Minimum score for the digest</Label>
              <Input
                id="min-score-digest"
                type="number"
                min={0}
                max={100}
                className="w-28"
                value={state.minScoreDigest}
                aria-invalid={!!errors.min_score_digest || undefined}
                onChange={(event) =>
                  dispatch({ type: "minScore", which: "digest", value: Number(event.target.value) })
                }
              />
              {errors.min_score_digest ? (
                <p role="alert" data-testid="min-score-error" className="text-xs text-destructive">
                  {errors.min_score_digest}
                </p>
              ) : null}
            </div>

            <div className="grid gap-1.5">
              <p className="text-sm font-medium">Web push</p>
              <PushOptIn />
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Email opt-outs</CardTitle>
          <CardDescription>
            Categories you unsubscribed from using the link in an email. Opting out silences the
            email only — the bell and Slack keep the record.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {state.unsubscribedCategories.length ? (
            <ul className="flex flex-wrap gap-1.5" data-testid="unsubscribed-categories">
              {state.unsubscribedCategories.map((category) => (
                <li key={category}>
                  <Badge variant="outline">{categoryLabel(category)}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="unsubscribed-categories">
              None — every category can still email you.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={saving || !dirty || Object.keys(errors).length > 0}>
          {saving ? "Saving…" : "Save preferences"}
        </Button>
        {dirty ? <span className="text-sm text-muted-foreground">Unsaved changes.</span> : null}
      </div>
    </form>
  );
}
