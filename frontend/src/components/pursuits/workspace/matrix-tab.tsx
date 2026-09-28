"use client";

import Link from "next/link";
import * as React from "react";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";

import { OwnerAvatar, type OwnerLookup } from "@/components/pipeline/pursuit-card";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { DocumentOut } from "@/lib/opportunities/api";
import type { FormatRules, Matrix, MatrixRow } from "@/lib/pursuits/workspace-api";

const features = tableFeatures({});
const column = createColumnHelper<typeof features, MatrixRow>();

type Meta = {
  lookup?: OwnerLookup;
  documents: DocumentOut[];
  opportunityId: string;
};

const meta = (ctx: { table: { options: { meta?: unknown } } }) => ctx.table.options.meta as Meta;

const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  complete: "default",
  drafted: "secondary",
  open: "outline",
  blocked: "destructive",
  not_applicable: "outline",
};

/**
 * The document + page citation as a link (SPEC 8: "every extracted
 * requirement carries its document + page citation, clickable in the UI").
 * There is no page-text route, so the link opens the source file at that page
 * (`#page=N`, honoured by every PDF viewer) and falls back to the
 * opportunity's document list when the row's document is not listed (OQ-148).
 */
export function citationHref(
  documents: readonly DocumentOut[],
  opportunityId: string,
  documentId: string,
  page: number,
): string {
  const document = documents.find((row) => row.id === documentId);
  if (!document) return `/app/opportunities/${opportunityId}`;
  const url = (document as DocumentOut & { download_url?: string | null }).download_url || document.url;
  return page > 0 ? `${url}#page=${page}` : url;
}

const columns = column.columns([
  column.accessor("req_id", {
    id: "req_id",
    header: "Req",
    cell: (ctx) => <span className="font-mono text-xs">{ctx.getValue()}</span>,
  }),
  column.accessor("text", {
    id: "text",
    header: "Requirement",
    cell: (ctx) => (
      <span className="line-clamp-3 max-w-md whitespace-normal">{ctx.getValue()}</span>
    ),
  }),
  column.accessor("section", {
    id: "section",
    header: "Section",
    cell: (ctx) => (
      <span className="whitespace-normal">
        {ctx.getValue()}
        {ctx.row.original.volume ? (
          <span className="block text-xs text-muted-foreground">{ctx.row.original.volume}</span>
        ) : null}
      </span>
    ),
  }),
  column.accessor("owner_user_id", {
    id: "owner",
    header: "Owner",
    cell: (ctx) => <OwnerAvatar userId={ctx.getValue()} lookup={meta(ctx).lookup} />,
  }),
  column.accessor("status", {
    id: "status",
    header: "Status",
    cell: (ctx) => (
      <Badge variant={STATUS_VARIANT[ctx.getValue()] ?? "outline"} className="capitalize">
        {String(ctx.getValue()).replace(/_/g, " ")}
      </Badge>
    ),
  }),
  column.display({
    id: "citation",
    header: "Citation",
    cell: (ctx) => {
      const row = ctx.row.original;
      const { documents, opportunityId } = meta(ctx);
      return (
        <a
          href={citationHref(documents, opportunityId, row.document_id, row.page)}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="matrix-citation"
          title={row.quote}
          className="whitespace-nowrap underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          page {row.page}
        </a>
      );
    },
  }),
]);

function FormatRulesCard({ rules }: { rules: FormatRules | null | undefined }) {
  if (!rules) return null;
  const items: [string, string | number | null | undefined][] = [
    ["Page limit", rules.page_limit],
    ["Font", rules.font ? `${rules.font}${rules.font_size_pt ? ` ${rules.font_size_pt} pt` : ""}` : null],
    ["Margins", rules.margins],
    ["File types", rules.file_types?.length ? rules.file_types.join(", ") : null],
    ["File naming", rules.file_naming],
    ["Copies", rules.copies],
    ["Portal", rules.portal],
    ["Submission", rules.submission_method],
    ["Email", rules.email],
  ];
  const known = items.filter(([, value]) => value !== null && value !== undefined && value !== "");
  return (
    <Card data-testid="format-rules">
      <CardHeader>
        <CardTitle className="text-sm">Format rules</CardTitle>
        <CardDescription>Extracted from the solicitation; exports follow them.</CardDescription>
      </CardHeader>
      <CardContent>
        {known.length === 0 ? (
          <p className="text-sm text-muted-foreground">The solicitation states no format rules.</p>
        ) : (
          <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {known.map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
                <dd className="text-sm">{String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

export function MatrixTab({
  matrix,
  error,
  documents,
  opportunityId,
  lookup,
  recheckRequired,
}: {
  matrix: Matrix | null;
  error: string | null;
  documents: DocumentOut[];
  opportunityId: string;
  lookup?: OwnerLookup;
  recheckRequired: boolean;
}) {
  const rows = React.useMemo(() => matrix?.items ?? [], [matrix]);
  const tableMeta = React.useMemo<Meta>(
    () => ({ lookup, documents, opportunityId }),
    [lookup, documents, opportunityId],
  );
  const table = useTable({
    features,
    columns,
    data: rows,
    getRowId: (row) => row.id,
    meta: tableMeta as never,
  });

  return (
    <div className="grid gap-4" data-testid="matrix-tab">
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {recheckRequired ? (
        <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
          The solicitation was amended after this matrix was built. Re-run the matrix agent.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span data-testid="matrix-count">{rows.length} requirements</span>
        <Badge variant="outline">
          Read-only: the API has no matrix row update route yet
        </Badge>
        {matrix?.generated_at ? (
          <span>Built {new Date(matrix.generated_at).toLocaleString()}</span>
        ) : null}
      </div>

      <div className="overflow-x-auto rounded-xl border">
        <Table data-testid="matrix-table">
          <caption className="sr-only">
            Every extracted requirement with the section that answers it, its owner, its status and
            its document page citation
          </caption>
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
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground">
                  No requirements yet. Run the extract and matrix agents from the header.
                </TableCell>
              </TableRow>
            ) : null}
            {table.getRowModel().rows.map((row) => (
              <TableRow key={row.id} data-testid="matrix-row">
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

      <FormatRulesCard rules={matrix?.format_rules} />

      <p className="text-xs text-muted-foreground">
        A citation opens the source document at its page.{" "}
        <Link href={`/app/opportunities/${opportunityId}`} className="underline underline-offset-4">
          All documents for this notice
        </Link>
      </p>
    </div>
  );
}
