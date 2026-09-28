"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Board } from "@/components/pipeline/board";
import { PipelineFilterBar, type FilterPatch } from "@/components/pipeline/pipeline-filters";
import { PipelineTable } from "@/components/pipeline/pipeline-table";
import { useNow, useUserTimeZone } from "@/components/opportunities/due-time";
import { Pagination } from "@/components/opportunities/results-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import {
  BOARD_CAP,
  DEFAULT_PIPELINE_FILTERS,
  needsClientOwnerFilter,
  parsePipelineFilters,
  serializePipelineFilters,
  toPursuitQuery,
  type PipelineFilters,
  type PipelineView,
} from "@/lib/pursuits/filters";
import {
  conflictReason,
  listPursuits,
  loadBoard,
  patchPursuit,
  type PursuitListItem,
} from "@/lib/pursuits/api";
import { stageLabel } from "@/lib/pursuits/stages";
import { listMembers, type Member } from "@/lib/settings/api";
import { cn } from "@/lib/utils";

type Loaded = {
  items: PursuitListItem[];
  byStage: Record<string, number>;
  total: number;
  pages: number;
  capped: boolean;
};

type LoadState =
  | { kind: "loading"; previous: Loaded | null }
  | { kind: "ready"; data: Loaded }
  | { kind: "error"; message: string; previous: Loaded | null };

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

function ViewToggle({ view, onView }: { view: PipelineView; onView: (view: PipelineView) => void }) {
  return (
    <div role="group" aria-label="View" className="inline-flex rounded-lg border p-0.5">
      {(["board", "table"] as const).map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={view === option}
          onClick={() => onView(option)}
          data-testid={`view-${option}`}
          className={cn(
            "rounded-md px-3 py-1 text-sm capitalize transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
            view === option ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

/** SPEC 10.4 screen 5: the pipeline board and table over one shared filter state. */
export function PipelineScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const now = useNow();
  const userTz = useUserTimeZone();

  const query = params.toString();
  const filters = React.useMemo(() => parsePipelineFilters(query), [query]);
  const [state, setState] = React.useState<LoadState>({ kind: "loading", previous: null });
  const [members, setMembers] = React.useState<Member[]>([]);
  const [reloadToken, setReloadToken] = React.useState(0);
  const latest = React.useRef<Loaded | null>(null);

  const navigate = React.useCallback(
    (next: PipelineFilters) => {
      const search = serializePipelineFilters(next);
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
    listMembers(controller.signal)
      .then(setMembers)
      .catch(() => setMembers([]));
    return () => controller.abort();
  }, []);

  const view = filters.view;
  // The board loads every page of the filter; the table loads one page. The
  // dependency is the serialized filter set, so a pure `view` switch reloads
  // (the two views ask for different page sizes) but a re-render does not.
  const key = React.useMemo(
    () => JSON.stringify([view, toPursuitQuery(filters), needsClientOwnerFilter(filters)]),
    [filters, view],
  );

  React.useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading", previous: latest.current });
    const request =
      view === "board"
        ? loadBoard(filters, controller.signal).then((board) => ({
            items: board.items,
            byStage: board.byStage,
            total: board.total,
            pages: 1,
            capped: board.capped,
          }))
        : listPursuits(toPursuitQuery(filters), controller.signal).then((page) => ({
            items: needsClientOwnerFilter(filters)
              ? page.items.filter((item) => !item.owner_user_id)
              : page.items,
            byStage: page.by_stage ?? {},
            total: page.total,
            pages: Math.max(page.pages, 1),
            capped: false,
          }));
    request
      .then((data) => {
        latest.current = data;
        setState({ kind: "ready", data });
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message: describe(caught, "The pipeline could not be read"),
          previous: latest.current,
        });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reloadToken]);

  const data = state.kind === "ready" ? state.data : state.previous;

  const lookup = React.useCallback(
    (userId: string | null) => {
      if (!userId) return null;
      const member = members.find((row) => row.user_id === userId);
      return member ? { name: member.name ?? null, email: member.email } : null;
    },
    [members],
  );

  /**
   * Optimistic stage move: the card jumps to the new column at once and the
   * PATCH follows. A 409 puts it back and shows the server's `reason` — the
   * rules live on the server (`app.core.pursuit_stages`) and the board never
   * second-guesses them.
   */
  const move = React.useCallback(
    async (pursuitId: string, toStage: string) => {
      const current = latest.current;
      if (!current) return;
      const before = current.items.find((item) => item.id === pursuitId);
      if (!before || before.stage === toStage) return;

      const apply = (items: PursuitListItem[], stage: string) =>
        items.map((item) => (item.id === pursuitId ? { ...item, stage } : item));
      const shift = (byStage: Record<string, number>, from: string, to: string) => ({
        ...byStage,
        [from]: Math.max((byStage[from] ?? 0) - 1, 0),
        [to]: (byStage[to] ?? 0) + 1,
      });

      const optimistic: Loaded = {
        ...current,
        items: apply(current.items, toStage),
        byStage: shift(current.byStage, before.stage, toStage),
      };
      latest.current = optimistic;
      setState({ kind: "ready", data: optimistic });

      try {
        const updated = await patchPursuit(pursuitId, { stage: toStage });
        const settled: Loaded = {
          ...optimistic,
          items: optimistic.items.map((item) =>
            item.id === pursuitId ? { ...item, stage: updated.stage, decision: updated.decision } : item,
          ),
        };
        latest.current = settled;
        setState({ kind: "ready", data: settled });
        toast.success(`${before.title} moved to ${stageLabel(toStage)}`);
      } catch (caught) {
        latest.current = current;
        setState({ kind: "ready", data: current });
        const reason = conflictReason(caught);
        toast.error(reason ?? describe(caught, "The move was refused"));
      }
    },
    [],
  );

  const total = data?.total ?? null;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Pipeline</h1>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {total === null
              ? "Every pursuit your tenant is tracking."
              : `${total.toLocaleString()} ${total === 1 ? "pursuit" : "pursuits"}.`}
          </p>
        </div>
        <ViewToggle view={view} onView={(next) => navigate({ ...filters, view: next, page: 1 })} />
      </div>

      <PipelineFilterBar
        filters={filters}
        members={members}
        onChange={patch}
        onReset={() => navigate({ ...DEFAULT_PIPELINE_FILTERS, view })}
      />

      {state.kind === "error" ? (
        <Card role="alert" data-testid="pipeline-error">
          <CardHeader>
            <CardTitle>Could not load the pipeline</CardTitle>
            <CardDescription>{state.message}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button type="button" variant="outline" size="sm" onClick={() => setReloadToken((n) => n + 1)}>
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {data?.capped ? (
        <p role="status" data-testid="board-cap-notice" className="rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
          Showing the first {BOARD_CAP.toLocaleString()} of {data.total.toLocaleString()} pursuits. Narrow the
          filters, or switch to the table, to see the rest.
        </p>
      ) : null}

      {state.kind === "loading" && !data ? (
        <div role="status" aria-live="polite" className="grid gap-2 rounded-xl border p-4">
          <span className="sr-only">Loading the pipeline</span>
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="h-9 animate-pulse rounded-md bg-muted" aria-hidden="true" />
          ))}
        </div>
      ) : null}

      {data ? (
        <div
          aria-busy={state.kind === "loading"}
          className={state.kind === "loading" ? "opacity-60 transition-opacity" : undefined}
        >
          {view === "board" ? (
            <Board
              items={data.items}
              byStage={data.byStage}
              now={now}
              userTz={userTz}
              lookup={lookup}
              onMove={move}
              draggable={state.kind !== "loading"}
            />
          ) : (
            <div className="grid gap-3">
              <PipelineTable items={data.items} now={now} lookup={lookup} />
              <Pagination
                page={filters.page}
                pages={data.pages}
                total={data.total}
                pageSize={filters.page_size}
                onPage={(page) => navigate({ ...filters, page })}
                onPageSize={(page_size) => navigate({ ...filters, page_size, page: 1 })}
              />
            </div>
          )}
        </div>
      ) : null}

      {data && data.items.length === 0 && state.kind === "ready" ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          No pursuits match these filters. Open an opportunity and choose Pursue or Watch to put it on the board.
        </p>
      ) : null}
    </div>
  );
}
