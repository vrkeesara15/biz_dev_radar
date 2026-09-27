"use client";

import * as React from "react";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";

import { EmptyNote, ErrorNote, Pager, describeError } from "@/components/admin/common";
import { TenantDrawer } from "@/components/admin/tenant-drawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listTenants, type TenantPage, type TenantRow } from "@/lib/admin/api";
import { formatTimestamp } from "@/lib/admin/format";

const features = tableFeatures({});
const column = createColumnHelper<typeof features, TenantRow>();

const columns = column.columns([
  column.accessor("name", {
    id: "name",
    header: "Tenant",
    cell: (ctx) => (
      <span className="flex flex-col leading-tight">
        <span className="font-medium">{ctx.getValue()}</span>
        <span className="text-xs text-muted-foreground">{ctx.row.original.slug}</span>
      </span>
    ),
  }),
  column.accessor("plan", {
    id: "plan",
    header: "Plan",
    cell: (ctx) => <Badge variant="outline">{ctx.getValue()}</Badge>,
  }),
  column.accessor("region", {
    id: "region",
    header: "Region",
    cell: (ctx) => <span className="uppercase">{ctx.getValue()}</span>,
  }),
  column.accessor("is_internal", {
    id: "is_internal",
    header: "Internal",
    cell: (ctx) => (ctx.getValue() ? "Yes" : "—"),
  }),
  column.accessor("member_count", {
    id: "member_count",
    header: "Members",
    cell: (ctx) => <span className="tabular-nums">{ctx.getValue()}</span>,
  }),
  column.accessor("profile_count", {
    id: "profile_count",
    header: "Profiles",
    cell: (ctx) => <span className="tabular-nums">{ctx.getValue()}</span>,
  }),
  column.accessor("created_at", {
    id: "created_at",
    header: "Created",
    cell: (ctx) => (
      <span className="whitespace-nowrap">{formatTimestamp(ctx.getValue())}</span>
    ),
  }),
  column.accessor("deleted_at", {
    id: "deleted_at",
    header: "Deleted",
    cell: (ctx) =>
      ctx.getValue() ? (
        <span className="text-destructive">{formatTimestamp(ctx.getValue())}</span>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  }),
]);

export type TenantsTabProps = {
  period: string;
  selected: string | null;
  onSelect: (tenantId: string | null) => void;
};

/** Tenant list (SPEC 10.3 GET /admin/tenants) plus the detail drawer. */
export function TenantsTab({ period, selected, onSelect }: TenantsTabProps) {
  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [data, setData] = React.useState<TenantPage | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [reload, setReload] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    setError(null);
    listTenants({ q: search || undefined, page, page_size: 25 }, controller.signal)
      .then((result) => setData(result))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(describeError(err, "Could not load the tenant list."));
      });
    return () => controller.abort();
  }, [search, page, reload]);

  const table = useTable({
    features,
    columns,
    data: data?.items ?? [],
    getRowId: (row) => row.id,
  });

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      <div className="min-w-0 flex-1 grid gap-4">
        <form
          className="flex max-w-sm items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setPage(1);
            setSearch(query.trim());
          }}
        >
          <Input
            aria-label="Search tenants"
            placeholder="Search by name or slug"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button type="submit" variant="outline" size="sm">
            Search
          </Button>
        </form>

        {error ? <ErrorNote message={error} /> : null}
        {!error && !data ? <EmptyNote>Loading tenants…</EmptyNote> : null}
        {data && !data.items.length ? <EmptyNote>No tenants match.</EmptyNote> : null}

        {data && data.items.length ? (
          <>
            <div className="rounded-xl border">
              <Table data-testid="tenants-table">
                <TableHeader>
                  {table.getHeaderGroups().map((headerGroup) => (
                    <TableRow key={headerGroup.id} className="hover:bg-transparent">
                      {headerGroup.headers.map((header) => (
                        <TableHead
                          key={header.id}
                          scope="col"
                          className="px-3 text-xs tracking-wide uppercase text-muted-foreground"
                        >
                          {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                        </TableHead>
                      ))}
                      <TableHead scope="col" className="px-3">
                        <span className="sr-only">Actions</span>
                      </TableHead>
                    </TableRow>
                  ))}
                </TableHeader>
                <TableBody>
                  {table.getRowModel().rows.map((row) => (
                    <TableRow
                      key={row.id}
                      data-testid="tenant-row"
                      data-slug={row.original.slug}
                      data-selected={row.id === selected ? "true" : undefined}
                    >
                      {row.getAllCells().map((cell) => (
                        <TableCell key={cell.id} className="px-3 py-2.5 align-top">
                          <table.FlexRender cell={cell} />
                        </TableCell>
                      ))}
                      <TableCell className="px-3 py-2.5 text-right align-top">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => onSelect(row.id === selected ? null : row.id)}
                        >
                          {row.id === selected ? "Hide" : "Manage"}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <Pager
              label="Tenant pages"
              page={data.page}
              pages={data.pages}
              total={data.total}
              onPage={setPage}
            />
          </>
        ) : null}
      </div>

      {selected ? (
        <TenantDrawer
          tenantId={selected}
          period={period}
          onClose={() => onSelect(null)}
          onChanged={() => setReload((n) => n + 1)}
        />
      ) : null}
    </div>
  );
}
