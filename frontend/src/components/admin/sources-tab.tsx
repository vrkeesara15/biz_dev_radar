"use client";

import * as React from "react";
import { toast } from "sonner";

import { EmptyNote, ErrorNote, StatusBadge, describeError } from "@/components/admin/common";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listSources, runSourceNow, type AdminSource } from "@/lib/admin/api";
import { formatTimestamp, relativeTime } from "@/lib/admin/format";

export type SourcesTabProps = {
  /** Open the run-history tab for one adapter. */
  onOpenRuns: (sourceId: string) => void;
};

/** Adapter health table with "Run now" (SPEC 10.4 screen 9, 10.3 POST /admin/sources/{id}/run). */
export function SourcesTab({ onOpenRuns }: SourcesTabProps) {
  const [sources, setSources] = React.useState<AdminSource[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [running, setRunning] = React.useState<string | null>(null);
  const [reload, setReload] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setError(null);
    listSources(controller.signal)
      .then((rows) => setSources(rows))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(err, "Could not load the source list."));
      });
    return () => controller.abort();
  }, [reload]);

  const run = async (sourceId: string) => {
    setRunning(sourceId);
    try {
      const result = await runSourceNow(sourceId);
      toast.success(
        result.mode === "queued"
          ? `Queued a run of ${sourceId}`
          : `Ran ${sourceId} inline (no worker answered)`,
      );
      setReload((n) => n + 1);
    } catch (err) {
      toast.error(describeError(err, `Could not run ${sourceId}.`));
    } finally {
      setRunning(null);
    }
  };

  if (error) return <ErrorNote message={error} />;
  if (!sources) return <EmptyNote>Loading adapters…</EmptyNote>;
  if (!sources.length) return <EmptyNote>No adapters are registered.</EmptyNote>;

  return (
    <div className="rounded-xl border">
      <Table data-testid="sources-table">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {["Source", "Region", "Health", "Last run", "Failures", "Schedule", ""].map(
              (head, index) => (
                <TableHead
                  key={head || `actions-${index}`}
                  scope="col"
                  className="px-3 text-xs tracking-wide uppercase text-muted-foreground"
                >
                  {head || <span className="sr-only">Actions</span>}
                </TableHead>
              ),
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sources.map((source) => (
            <TableRow key={source.source_id} data-testid="source-row" data-source={source.source_id}>
              <TableCell className="px-3 py-2.5 align-top font-medium">
                {source.source_id}
                {source.enabled ? null : (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">disabled</span>
                )}
              </TableCell>
              <TableCell className="px-3 py-2.5 align-top uppercase">{source.region}</TableCell>
              <TableCell className="px-3 py-2.5 align-top">
                <StatusBadge status={source.health_status} />
                {source.health_message ? (
                  <p className="mt-1 max-w-72 text-xs text-muted-foreground">
                    {source.health_message}
                  </p>
                ) : null}
              </TableCell>
              <TableCell className="px-3 py-2.5 align-top">
                <span title={formatTimestamp(source.last_run_at)}>
                  {relativeTime(source.last_run_at)}
                </span>
                {source.last_status ? (
                  <p className="text-xs text-muted-foreground">{source.last_status}</p>
                ) : null}
              </TableCell>
              <TableCell
                className="px-3 py-2.5 align-top tabular-nums"
                data-testid="consecutive-failures"
              >
                {source.consecutive_failures}
              </TableCell>
              <TableCell className="px-3 py-2.5 align-top font-mono text-xs">
                {source.schedule}
              </TableCell>
              <TableCell className="px-3 py-2.5 align-top text-right">
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => onOpenRuns(source.source_id)}
                  >
                    History
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={running !== null || !source.registered}
                    onClick={() => run(source.source_id)}
                  >
                    {running === source.source_id ? "Running…" : "Run now"}
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
