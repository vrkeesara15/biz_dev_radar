"use client";

import Link from "next/link";
import * as React from "react";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";

import { NoticeTypeBadge, ScoreBadge } from "@/components/opportunities/badges";
import { MatchFeedback } from "@/components/opportunities/match-feedback";
import { SourceLink } from "@/components/opportunities/attribution-footer";
import { DueTime } from "@/components/opportunities/due-time";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { normalizeMatch, type OpportunityItem } from "@/lib/opportunities/api";
import { buyerPath, formatValueRange } from "@/lib/opportunities/format";
import { cn } from "@/lib/utils";

const features = tableFeatures({});
const column = createColumnHelper<typeof features, OpportunityItem>();

function ValueCell({ item }: { item: OpportunityItem }) {
  const value = formatValueRange(item);
  if (!value.primary) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex flex-col leading-tight">
      <span className="tabular-nums">{value.primary}</span>
      {value.usd ? <span className="text-xs tabular-nums text-muted-foreground">≈ {value.usd}</span> : null}
    </span>
  );
}

function BuyerCell({ item }: { item: OpportunityItem }) {
  const path = buyerPath(item);
  if (!path.length) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex max-w-56 flex-col leading-tight" title={path.join(" › ")}>
      <span className="truncate">{path[0]}</span>
      {path.length > 1 ? <span className="truncate text-xs text-muted-foreground">{path.slice(1).join(" › ")}</span> : null}
    </span>
  );
}

const columns = column.columns([
  column.accessor((row) => normalizeMatch(row.match)?.score ?? null, {
    id: "score",
    header: "Score",
    cell: (ctx) => <ScoreBadge score={ctx.getValue()} />,
  }),
  column.accessor("title", {
    id: "title",
    header: "Title",
    cell: (ctx) => {
      const item = ctx.row.original;
      return (
        <span className="flex max-w-md flex-col leading-tight">
          <Link
            href={`/app/opportunities/${item.id}`}
            className="line-clamp-2 whitespace-normal font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {ctx.getValue()}
          </Link>
          {item.solicitation_number ? (
            <span className="text-xs text-muted-foreground">{item.solicitation_number}</span>
          ) : null}
        </span>
      );
    },
  }),
  column.display({
    id: "buyer",
    header: "Buyer",
    cell: (ctx) => <BuyerCell item={ctx.row.original} />,
  }),
  column.display({
    id: "value",
    header: "Value",
    cell: (ctx) => <ValueCell item={ctx.row.original} />,
  }),
  column.accessor("response_due_at", {
    id: "due",
    header: "Due",
    cell: (ctx) => {
      const item = ctx.row.original;
      const now = (ctx.table.options.meta as TableMeta | undefined)?.now;
      return <DueTime value={ctx.getValue()} sourceTz={item.source_tz} now={now} showCountdown />;
    },
  }),
  column.accessor("notice_type", {
    id: "type",
    header: "Type",
    cell: (ctx) => <NoticeTypeBadge type={ctx.getValue()} />,
  }),
  column.display({
    id: "source",
    header: "Source",
    cell: (ctx) => {
      const item = ctx.row.original;
      return <SourceLink attribution={item.attribution} url={item.attribution.source_url} className="text-sm" />;
    },
  }),
  column.display({
    id: "feedback",
    header: () => <span className="sr-only">Match feedback</span>,
    cell: (ctx) => {
      const item = ctx.row.original;
      return <MatchFeedback opportunityId={item.id} title={item.title} />;
    },
  }),
]);

type TableMeta = { now: Date };

export function ResultsTable({ items, now, className }: { items: OpportunityItem[]; now: Date; className?: string }) {
  const meta = React.useMemo<TableMeta>(() => ({ now }), [now]);
  const table = useTable({
    features,
    columns,
    data: items,
    getRowId: (row) => row.id,
    meta: meta as never,
  });

  return (
    <div className={cn("rounded-xl border", className)}>
      <Table data-testid="results-table">
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => (
                <TableHead key={header.id} scope="col" className="px-3 text-xs uppercase tracking-wide text-muted-foreground">
                  {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id} data-testid="result-row" data-id={row.id}>
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

export type PaginationProps = {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  onPageSize: (pageSize: number) => void;
  className?: string;
};

export function Pagination({ page, pages, total, pageSize, onPage, onPageSize, className }: PaginationProps) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return (
    <nav aria-label="Pagination" className={cn("flex flex-wrap items-center justify-between gap-3 text-sm", className)}>
      <p className="text-muted-foreground" aria-live="polite">
        {total === 0 ? "No results" : `Showing ${first}–${last} of ${total.toLocaleString()}`}
      </p>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-muted-foreground">
          <span>Rows</span>
          <NativeSelect
            aria-label="Rows per page"
            className="w-20"
            value={pageSize}
            onChange={(event) => onPageSize(Number(event.target.value))}
          >
            {[25, 50, 100].map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </NativeSelect>
        </label>
        <span className="tabular-nums text-muted-foreground">
          Page {page} of {Math.max(pages, 1)}
        </span>
        <Button type="button" variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button type="button" variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </nav>
  );
}
