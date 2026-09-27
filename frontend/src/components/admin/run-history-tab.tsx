"use client";

import * as React from "react";

import {
  EmptyNote,
  ErrorNote,
  Pager,
  StatusBadge,
  describeError,
} from "@/components/admin/common";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listSourceRuns, listSources, type SourceRunPage } from "@/lib/admin/api";
import { formatDuration, formatTimestamp } from "@/lib/admin/format";

export type RunHistoryTabProps = {
  sourceId: string | null;
  onSource: (sourceId: string) => void;
};

/** source_runs history for one adapter (status, counts, errors) — SPEC 10.2 / screen 9. */
export function RunHistoryTab({ sourceId, onSource }: RunHistoryTabProps) {
  const [sourceIds, setSourceIds] = React.useState<string[]>([]);
  const [page, setPage] = React.useState(1);
  const [data, setData] = React.useState<SourceRunPage | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    listSources(controller.signal)
      .then((rows) => {
        const ids = rows.map((row) => row.source_id);
        setSourceIds(ids);
        if (!sourceId && ids.length) onSource(ids[0]);
      })
      .catch(() => {
        /* the sources tab already reports this */
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => setPage(1), [sourceId]);

  React.useEffect(() => {
    if (!sourceId) return;
    const controller = new AbortController();
    setError(null);
    listSourceRuns(sourceId, { page, page_size: 25 }, controller.signal)
      .then((result) => setData(result))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(describeError(err, `Could not load runs for ${sourceId}.`));
      });
    return () => controller.abort();
  }, [sourceId, page]);

  return (
    <div className="grid gap-4">
      <label className="flex max-w-xs items-center gap-2 text-sm">
        <span className="text-muted-foreground">Source</span>
        <NativeSelect
          aria-label="Source"
          value={sourceId ?? ""}
          onChange={(event) => onSource(event.target.value)}
        >
          {sourceId && !sourceIds.includes(sourceId) ? (
            <option value={sourceId}>{sourceId}</option>
          ) : null}
          {sourceIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </NativeSelect>
      </label>

      {error ? <ErrorNote message={error} /> : null}
      {!error && !data ? <EmptyNote>Loading run history…</EmptyNote> : null}
      {data && !data.items.length ? (
        <EmptyNote>This adapter has not run yet.</EmptyNote>
      ) : null}

      {data && data.items.length ? (
        <>
          <div className="rounded-xl border">
            <Table data-testid="runs-table">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {["Started", "Status", "Duration", "Fetched", "Upserted", "Errors"].map(
                    (head) => (
                      <TableHead
                        key={head}
                        scope="col"
                        className="px-3 text-xs tracking-wide uppercase text-muted-foreground"
                      >
                        {head}
                      </TableHead>
                    ),
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((run) => (
                  <TableRow key={run.id} data-testid="run-row">
                    <TableCell className="px-3 py-2.5 align-top whitespace-nowrap">
                      {formatTimestamp(run.started_at)}
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top">
                      <StatusBadge status={run.status} />
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top tabular-nums">
                      {formatDuration(run.started_at, run.finished_at)}
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top tabular-nums">
                      {run.fetched.toLocaleString()}
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top tabular-nums">
                      {run.upserted.toLocaleString()}
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top">
                      {run.error_count ? (
                        <span className="text-destructive">
                          {run.error_count}
                          {run.last_error ? (
                            <span className="ml-2 text-xs text-muted-foreground">
                              {run.last_error}
                            </span>
                          ) : null}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pager
            label="Run history pages"
            page={data.page}
            pages={data.pages}
            total={data.total}
            onPage={setPage}
          />
        </>
      ) : null}
    </div>
  );
}
