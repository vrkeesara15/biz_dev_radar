"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { DisclaimerNote } from "@/components/opportunities/attribution-footer";
import { useNow } from "@/components/opportunities/due-time";
import { FilterSidebar, type FilterPatch } from "@/components/opportunities/filter-sidebar";
import { Pagination, ResultsTable } from "@/components/opportunities/results-table";
import { SavedSearchBar } from "@/components/opportunities/saved-search-bar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api/browser";
import { ApiError, searchOpportunities, type OpportunityPage } from "@/lib/opportunities/api";
import {
  DEFAULT_FILTERS,
  activeFilterCount,
  parseFilters,
  serializeFilters,
  type OpportunityFilters,
} from "@/lib/opportunities/filters";

type LoadState =
  | { kind: "loading"; previous: OpportunityPage | null }
  | { kind: "ready"; page: OpportunityPage }
  | { kind: "error"; message: string; previous: OpportunityPage | null };

export function SearchScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const now = useNow();

  const query = params.toString();
  const filters = React.useMemo(() => parseFilters(query), [query]);
  const [state, setState] = React.useState<LoadState>({ kind: "loading", previous: null });
  const [reloadToken, setReloadToken] = React.useState(0);
  const latest = React.useRef<OpportunityPage | null>(null);

  const navigate = React.useCallback(
    (next: OpportunityFilters) => {
      const search = serializeFilters(next);
      router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  const patch = React.useCallback(
    (change: FilterPatch) => navigate({ ...filters, ...change, page: 1 }),
    [filters, navigate],
  );

  React.useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading", previous: latest.current });
    searchOpportunities(filters, controller.signal)
      .then((page) => {
        latest.current = page;
        setState({ kind: "ready", page });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof ApiError
            ? errorMessage(error.body, `The search failed (${error.status}).`)
            : "The search failed. Check your connection and try again.";
        setState({ kind: "error", message, previous: latest.current });
      });
    return () => controller.abort();
  }, [filters, reloadToken]);

  const page = state.kind === "ready" ? state.page : state.previous;
  const total = page?.total ?? null;
  const active = activeFilterCount(filters);

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Opportunities</h1>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total === null
              ? "Public notices from every connected source."
              : `${total.toLocaleString()} ${total === 1 ? "notice" : "notices"}${active ? " match these filters" : ""}.`}
          </p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <FilterSidebar filters={filters} onChange={patch} onReset={() => navigate(DEFAULT_FILTERS)} />

        <div className="grid min-w-0 content-start gap-4">
          <SavedSearchBar filters={filters} onApply={(next) => navigate({ ...next, page: 1 })} />

          {state.kind === "error" ? (
            <Card role="alert" data-testid="results-error">
              <CardHeader>
                <CardTitle>Could not load opportunities</CardTitle>
                <CardDescription>{state.message}</CardDescription>
              </CardHeader>
              <CardContent>
                <Button type="button" variant="outline" size="sm" onClick={() => setReloadToken((n) => n + 1)}>
                  Retry
                </Button>
              </CardContent>
            </Card>
          ) : null}

          {page && page.items.length > 0 ? (
            <div aria-busy={state.kind === "loading"} className={state.kind === "loading" ? "opacity-60 transition-opacity" : undefined}>
              <ResultsTable items={page.items} now={now} />
            </div>
          ) : null}

          {state.kind === "loading" && !page ? (
            <div role="status" aria-live="polite" className="grid gap-2 rounded-xl border p-4">
              <span className="sr-only">Loading opportunities</span>
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="h-9 animate-pulse rounded-md bg-muted" aria-hidden="true" />
              ))}
            </div>
          ) : null}

          {state.kind === "ready" && page && page.items.length === 0 ? (
            <Card data-testid="results-empty">
              <CardHeader>
                <CardTitle>No opportunities match</CardTitle>
                <CardDescription>
                  {active
                    ? "Loosen a filter or clear them all. New notices arrive with every source run."
                    : "Nothing has been ingested yet. Sources run on their own schedule; check back shortly."}
                </CardDescription>
              </CardHeader>
              {active ? (
                <CardContent>
                  <Button type="button" variant="outline" size="sm" onClick={() => navigate(DEFAULT_FILTERS)}>
                    Clear filters
                  </Button>
                </CardContent>
              ) : null}
            </Card>
          ) : null}

          {page ? (
            <Pagination
              page={page.page}
              pages={page.pages}
              total={page.total}
              pageSize={page.page_size ?? filters.page_size}
              onPage={(next) => navigate({ ...filters, page: next })}
              onPageSize={(size) => navigate({ ...filters, page: 1, page_size: size })}
            />
          ) : null}

          <footer className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
            <DisclaimerNote />
            <p className="text-xs text-muted-foreground">
              Every row links to its official portal page; deadlines show the buyer&apos;s clock first, yours second.
            </p>
          </footer>
        </div>
      </div>
    </div>
  );
}
