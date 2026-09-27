"use client";

import { BookmarkIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { errorMessage } from "@/lib/api/browser";
import {
  NotAvailableError,
  SAVED_SEARCHES_UNAVAILABLE_MESSAGE,
  createSavedSearch,
  listSavedSearches,
  type SavedSearch,
} from "@/lib/opportunities/api";
import {
  describeFilters,
  filtersToRecord,
  parseFilters,
  sameSearch,
  searchHref,
  type OpportunityFilters,
} from "@/lib/opportunities/filters";
import { cn } from "@/lib/utils";

export type SavedSearchBarProps = {
  filters: OpportunityFilters;
  onApply: (filters: OpportunityFilters) => void;
  className?: string;
};

/**
 * Saved searches (SPEC 6 learning loop: any filter set can be saved and doubles
 * as an alert rule). The API lands with M4-08; until then GET/POST answer 404
 * and the bar says so instead of pretending.
 */
export function SavedSearchBar({ filters, onApply, className }: SavedSearchBarProps) {
  const [saved, setSaved] = React.useState<SavedSearch[]>([]);
  const [unavailable, setUnavailable] = React.useState(false);
  const [loaded, setLoaded] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    listSavedSearches()
      .then((items) => {
        if (!cancelled) setSaved(Array.isArray(items) ? items : []);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof NotAvailableError) setUnavailable(true);
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const description = describeFilters(filters);
  const href = searchHref(filters);
  const absoluteHref = typeof window === "undefined" ? href : new URL(href, window.location.origin).toString();

  const openDialog = () => {
    setName(description === "All opportunities" ? "" : description);
    setOpen(true);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const clean = name.trim();
    if (!clean) return;
    setSaving(true);
    try {
      const created = await createSavedSearch({ name: clean, filters: filtersToRecord(filters) });
      setSaved((list) => [...list, created]);
      setOpen(false);
      toast.success(`Saved “${created.name}”`);
    } catch (error) {
      if (error instanceof NotAvailableError) {
        setUnavailable(true);
        setOpen(false);
        toast.info(SAVED_SEARCHES_UNAVAILABLE_MESSAGE, {
          description: "Bookmark this page instead — the URL is the search.",
        });
      } else {
        toast.error(errorMessage((error as { body?: unknown })?.body, "Could not save the search"));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      aria-label="Saved searches"
      data-testid="saved-search-bar"
      className={cn("flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 px-3 py-2", className)}
    >
      <BookmarkIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="text-sm font-medium">Saved searches</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
        {saved.map((item) => {
          const parsed = parseFilters(item.filters);
          const active = sameSearch(parsed, filters);
          return (
            <Button
              key={item.id}
              type="button"
              variant={active ? "secondary" : "ghost"}
              size="xs"
              aria-pressed={active}
              title={describeFilters(parsed)}
              onClick={() => onApply(parsed)}
            >
              {item.name}
            </Button>
          );
        })}
        {loaded && !saved.length ? (
          <span className="text-xs text-muted-foreground">
            {unavailable ? SAVED_SEARCHES_UNAVAILABLE_MESSAGE : "None yet."}
          </span>
        ) : null}
      </div>
      <Button type="button" variant="outline" size="sm" onClick={openDialog}>
        Save this search
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <form onSubmit={submit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Save this search</DialogTitle>
              <DialogDescription>
                A saved search is an alert rule: new matches for it reach you on your chosen channels.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="saved-search-name">Name</Label>
              <Input
                id="saved-search-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Cloud RFPs in the US"
                required
                autoFocus
              />
            </div>
            <dl className="grid gap-1 text-xs">
              <dt className="text-muted-foreground">Filters</dt>
              <dd>{description}</dd>
              <dt className="mt-1 text-muted-foreground">Link</dt>
              <dd>
                <Input readOnly value={absoluteHref} aria-label="Search link" className="h-7 font-mono text-xs" onFocus={(e) => e.currentTarget.select()} />
              </dd>
            </dl>
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
              <Button type="submit" disabled={saving || !name.trim()}>
                {saving ? "Saving…" : "Save"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}
