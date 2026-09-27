"use client";

import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { errorMessage } from "@/lib/api/browser";
import { CHANNELS, CHANNEL_LABELS, type Channel } from "@/lib/notifications/prefs";
import {
  ALERT_RULES_UNAVAILABLE_MESSAGE,
  ApiError,
  NotAvailableError,
  SAVED_SEARCHES_UNAVAILABLE_MESSAGE,
  createAlertRule,
  deleteSavedSearch,
  listAlertRules,
  listSavedSearches,
  updateAlertRule,
  updateSavedSearch,
  type AlertRule,
  type AlertRuleMode,
  type SavedSearch,
} from "@/lib/opportunities/api";
import { describeFilters, parseFilters, searchHref } from "@/lib/opportunities/filters";

const DEFAULT_RULE_MIN_SCORE = 70;

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

/**
 * Settings > Saved searches (SPEC 6: "users can save any filter set; each
 * saved search is also an alert rule"). Renaming and deleting go to
 * /saved-searches/{id}; the alert rule beside each one carries the mode,
 * minimum score and channels.
 */
export function SavedSearchesScreen() {
  const [searches, setSearches] = React.useState<SavedSearch[]>([]);
  const [rules, setRules] = React.useState<AlertRule[]>([]);
  const [loaded, setLoaded] = React.useState(false);
  const [searchesUnavailable, setSearchesUnavailable] = React.useState(false);
  const [rulesUnavailable, setRulesUnavailable] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [renaming, setRenaming] = React.useState<SavedSearch | null>(null);
  const [renameValue, setRenameValue] = React.useState("");

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const items = await listSavedSearches();
        if (!cancelled) setSearches(Array.isArray(items) ? items : []);
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof NotAvailableError) setSearchesUnavailable(true);
        else setError(describe(caught, "Saved searches could not be read"));
      }
      try {
        const items = await listAlertRules();
        if (!cancelled) setRules(Array.isArray(items) ? items : []);
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof NotAvailableError) setRulesUnavailable(true);
      }
      if (!cancelled) setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const ruleFor = (search: SavedSearch) => rules.find((rule) => rule.saved_search_id === search.id) ?? null;

  const patchRule = async (search: SavedSearch, patch: Partial<AlertRule>) => {
    const existing = ruleFor(search);
    setBusy(search.id);
    try {
      if (existing) {
        const updated = await updateAlertRule(existing.id, patch);
        setRules((list) => list.map((rule) => (rule.id === existing.id ? { ...rule, ...updated } : rule)));
      } else {
        const created = await createAlertRule({
          saved_search_id: search.id,
          name: search.name,
          mode: "digest",
          min_score: DEFAULT_RULE_MIN_SCORE,
          channels: ["email"],
          enabled: true,
          ...patch,
        });
        setRules((list) => [...list, created]);
      }
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setRulesUnavailable(true);
        toast.info(ALERT_RULES_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(describe(caught, "Could not update the alert rule"));
      }
    } finally {
      setBusy(null);
    }
  };

  const rename = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!renaming) return;
    const name = renameValue.trim();
    if (!name) return;
    setBusy(renaming.id);
    try {
      const updated = await updateSavedSearch(renaming.id, { name });
      setSearches((list) => list.map((item) => (item.id === renaming.id ? { ...item, ...updated } : item)));
      setRenaming(null);
      toast.success("Saved search renamed");
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setSearchesUnavailable(true);
        setRenaming(null);
        toast.info(SAVED_SEARCHES_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(describe(caught, "Could not rename the search"));
      }
    } finally {
      setBusy(null);
    }
  };

  const remove = async (search: SavedSearch) => {
    setBusy(search.id);
    try {
      await deleteSavedSearch(search.id);
      setSearches((list) => list.filter((item) => item.id !== search.id));
      setRules((list) => list.filter((rule) => rule.saved_search_id !== search.id));
      toast.success(`Deleted “${search.name}”`);
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setSearchesUnavailable(true);
        toast.info(SAVED_SEARCHES_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(describe(caught, "Could not delete the search"));
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid gap-4" data-testid="saved-searches-screen">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Saved searches</h2>
          <p className="text-sm text-muted-foreground">
            Every saved search is an alert rule: new matches for it reach you on the channels below.
          </p>
        </div>
        <Button render={<Link href="/app/opportunities" />} variant="outline" size="sm">
          New search
        </Button>
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {searchesUnavailable ? (
        <p className="text-sm text-muted-foreground" data-testid="saved-searches-unavailable">
          {SAVED_SEARCHES_UNAVAILABLE_MESSAGE}. Until then the search URL is the search — bookmark it.
        </p>
      ) : null}
      {loaded && !searchesUnavailable && !searches.length ? (
        <p className="text-sm text-muted-foreground">
          None yet. Use “Save this search” on the{" "}
          <Link href="/app/opportunities" className="underline underline-offset-4">
            opportunities page
          </Link>
          .
        </p>
      ) : null}

      {searches.map((search) => {
        const filters = parseFilters(search.filters);
        const rule = ruleFor(search);
        const mode: AlertRuleMode = rule?.mode ?? "digest";
        const channels = (rule?.channels ?? ["email"]) as Channel[];
        return (
          <Card key={search.id} data-testid="saved-search-row" data-name={search.name}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center justify-between gap-2">
                <Link href={searchHref(filters)} className="underline-offset-4 hover:underline">
                  {search.name}
                </Link>
                <span className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() => {
                      setRenaming(search);
                      setRenameValue(search.name);
                    }}
                    aria-label={`Rename ${search.name}`}
                  >
                    Rename
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    disabled={busy === search.id}
                    onClick={() => void remove(search)}
                    aria-label={`Delete ${search.name}`}
                  >
                    Delete
                  </Button>
                </span>
              </CardTitle>
              <CardDescription>{describeFilters(filters)}</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              {rulesUnavailable ? (
                <p className="text-sm text-muted-foreground" data-testid="alert-rules-unavailable">
                  {ALERT_RULES_UNAVAILABLE_MESSAGE}.
                </p>
              ) : (
                <div className="flex flex-wrap items-end gap-4">
                  <div className="grid gap-1.5">
                    <Label htmlFor={`mode-${search.id}`}>Alert mode</Label>
                    <NativeSelect
                      id={`mode-${search.id}`}
                      className="w-36"
                      value={mode}
                      disabled={busy === search.id}
                      onChange={(event) =>
                        void patchRule(search, { mode: event.target.value as AlertRuleMode })
                      }
                    >
                      <option value="instant">Instant</option>
                      <option value="digest">Daily digest</option>
                    </NativeSelect>
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor={`min-score-${search.id}`}>Minimum score</Label>
                    <Input
                      id={`min-score-${search.id}`}
                      type="number"
                      min={0}
                      max={100}
                      className="w-24"
                      defaultValue={rule?.min_score ?? DEFAULT_RULE_MIN_SCORE}
                      disabled={busy === search.id}
                      onBlur={(event) => {
                        const value = Number(event.target.value);
                        if (!Number.isFinite(value)) return;
                        if (rule && rule.min_score === value) return;
                        void patchRule(search, { min_score: Math.min(100, Math.max(0, Math.round(value))) });
                      }}
                    />
                  </div>
                  <fieldset className="grid gap-1.5">
                    <legend className="text-sm font-medium">Channels</legend>
                    <div className="flex flex-wrap items-center gap-3">
                      {CHANNELS.map((channel) => {
                        const id = `channel-${search.id}-${channel}`;
                        const checked = channels.includes(channel);
                        return (
                          <span key={channel} className="flex items-center gap-1.5">
                            <Checkbox
                              id={id}
                              checked={checked}
                              disabled={busy === search.id}
                              onChange={() =>
                                void patchRule(search, {
                                  channels: checked
                                    ? channels.filter((entry) => entry !== channel)
                                    : [...channels, channel],
                                })
                              }
                            />
                            <Label htmlFor={id} className="text-sm font-normal">
                              {CHANNEL_LABELS[channel]}
                            </Label>
                          </span>
                        );
                      })}
                    </div>
                  </fieldset>
                  <span className="flex items-center gap-1.5">
                    <Checkbox
                      id={`enabled-${search.id}`}
                      checked={rule ? rule.enabled : false}
                      disabled={busy === search.id}
                      onChange={() => void patchRule(search, { enabled: !(rule?.enabled ?? false) })}
                    />
                    <Label htmlFor={`enabled-${search.id}`} className="text-sm font-normal">
                      Alerts on
                    </Label>
                  </span>
                  {!rule ? <Badge variant="outline">No rule yet</Badge> : null}
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}

      <Dialog open={renaming !== null} onOpenChange={(open) => (open ? null : setRenaming(null))}>
        <DialogContent>
          <form onSubmit={rename} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Rename saved search</DialogTitle>
              <DialogDescription>The filters stay as they are.</DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="rename-saved-search">Name</Label>
              <Input
                id="rename-saved-search"
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                required
                autoFocus
              />
            </div>
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
              <Button type="submit" disabled={!renameValue.trim()}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
