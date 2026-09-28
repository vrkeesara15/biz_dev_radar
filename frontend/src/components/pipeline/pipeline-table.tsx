"use client";

import Link from "next/link";
import * as React from "react";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";

import { OwnerAvatar, type OwnerLookup } from "@/components/pipeline/pursuit-card";
import { DueTime } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { countdown } from "@/lib/opportunities/dates";
import { formatValueRange } from "@/lib/opportunities/format";
import { REGION_LABELS, type PipelineRegion } from "@/lib/pursuits/filters";
import type { PursuitListItem } from "@/lib/pursuits/api";
import { stageLabel } from "@/lib/pursuits/stages";
import { cn } from "@/lib/utils";

const features = tableFeatures({});
const column = createColumnHelper<typeof features, PursuitListItem>();

type TableMeta = { now: Date; lookup?: OwnerLookup };

const meta = (ctx: { table: { options: { meta?: unknown } } }) => ctx.table.options.meta as TableMeta;

/** "3d ago" / "just now" from an activity timestamp. */
export function lastActivity(now: Date, at: string | null | undefined): string {
  if (!at) return "—";
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "—";
  if (date.getTime() > now.getTime()) return "just now";
  const text = countdown(date, now);
  return text === "due now" ? "just now" : `${text} ago`;
}

const columns = column.columns([
  column.accessor("stage", {
    id: "stage",
    header: "Stage",
    cell: (ctx) => <Badge variant="secondary">{stageLabel(ctx.getValue())}</Badge>,
  }),
  column.accessor("title", {
    id: "title",
    header: "Title",
    cell: (ctx) => (
      <Link
        href={`/app/pursuits/${ctx.row.original.id}`}
        className="line-clamp-2 max-w-md whitespace-normal font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {ctx.getValue()}
      </Link>
    ),
  }),
  column.accessor("buyer_org", {
    id: "buyer",
    header: "Buyer",
    cell: (ctx) => (
      <span className="line-clamp-2 max-w-48 text-muted-foreground">{ctx.getValue() ?? "—"}</span>
    ),
  }),
  column.accessor("owner_user_id", {
    id: "owner",
    header: "Owner",
    cell: (ctx) => <OwnerAvatar userId={ctx.getValue()} lookup={meta(ctx).lookup} />,
  }),
  column.display({
    id: "value",
    header: "Value",
    cell: (ctx) => {
      const value = formatValueRange(ctx.row.original);
      if (!value.primary) return <span className="text-muted-foreground">—</span>;
      return (
        <span className="inline-flex flex-col leading-tight">
          <span className="tabular-nums">{value.primary}</span>
          {value.usd ? <span className="text-xs tabular-nums text-muted-foreground">≈ {value.usd}</span> : null}
        </span>
      );
    },
  }),
  column.accessor("response_due_at", {
    id: "due",
    header: "Due",
    cell: (ctx) => (
      <DueTime
        value={ctx.getValue()}
        sourceTz={ctx.row.original.source_tz}
        now={meta(ctx).now}
        showCountdown
      />
    ),
  }),
  column.accessor("region", {
    id: "region",
    header: "Region",
    cell: (ctx) => (
      <span className="text-muted-foreground">
        {REGION_LABELS[ctx.getValue() as PipelineRegion] ?? String(ctx.getValue()).toUpperCase()}
      </span>
    ),
  }),
  column.accessor("activity_at", {
    id: "last_activity",
    header: "Last activity",
    cell: (ctx) => (
      <span className="whitespace-nowrap text-muted-foreground tabular-nums">
        {lastActivity(meta(ctx).now, ctx.getValue())}
      </span>
    ),
  }),
]);

export function PipelineTable({
  items,
  now,
  lookup,
  className,
}: {
  items: PursuitListItem[];
  now: Date;
  lookup?: OwnerLookup;
  className?: string;
}) {
  const tableMeta = React.useMemo<TableMeta>(() => ({ now, lookup }), [now, lookup]);
  const table = useTable({
    features,
    columns,
    data: items,
    getRowId: (row) => row.id,
    meta: tableMeta as never,
  });

  return (
    <div className={cn("overflow-x-auto rounded-xl border", className)}>
      <Table data-testid="pipeline-table">
        <caption className="sr-only">Pursuits matching the current filters</caption>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => (
                <TableHead
                  key={header.id}
                  scope="col"
                  className="px-3 text-xs uppercase tracking-wide text-muted-foreground"
                >
                  {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id} data-testid="pursuit-row" data-id={row.id}>
              {row.getAllCells().map((cell) => (
                <TableCell key={cell.id} className="px-3 py-2.5 align-top">
                  <table.FlexRender cell={cell} />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
