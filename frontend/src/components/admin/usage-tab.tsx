"use client";

import * as React from "react";

import { EmptyNote, ErrorNote, describeError } from "@/components/admin/common";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getUsage, type UsagePage } from "@/lib/admin/api";
import {
  barPercents,
  formatPeriod,
  formatTokens,
  formatUsd,
  recentPeriods,
} from "@/lib/admin/format";

export type UsageTabProps = {
  period: string;
  onPeriod: (period: string) => void;
};

/** Per-tenant LLM cost chart (SVG, no chart library) — SPEC 10.4 screen 9. */
function CostChart({ page }: { page: UsagePage }) {
  const rows = page.items.filter((row) => row.cost_microusd > 0).slice(0, 10);
  if (!rows.length) {
    return (
      <p className="text-sm text-muted-foreground">No LLM spend recorded for this month.</p>
    );
  }
  const widths = barPercents(rows.map((row) => row.cost_microusd));
  const rowHeight = 26;
  const height = rows.length * rowHeight;
  return (
    <svg
      role="img"
      aria-label={`LLM cost by tenant for ${formatPeriod(page.period)}`}
      data-testid="cost-chart"
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
      className="h-auto w-full"
      style={{ height }}
    >
      {rows.map((row, index) => (
        <g key={row.tenant_id} data-slug={row.slug}>
          <title>{`${row.slug}: ${formatUsd(row.cost_microusd)}`}</title>
          <rect
            x={0}
            y={index * rowHeight + 5}
            width={widths[index]}
            height={rowHeight - 12}
            rx={1}
            className="fill-primary/70"
          />
        </g>
      ))}
    </svg>
  );
}

/** Month picker + per-tenant tokens, cost and agent runs (SPEC 10.3 GET /admin/usage). */
export function UsageTab({ period, onPeriod }: UsageTabProps) {
  const [data, setData] = React.useState<UsagePage | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const months = React.useMemo(() => recentPeriods(12), []);
  const options = months.includes(period) ? months : [period, ...months];

  React.useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getUsage(period, controller.signal)
      .then((result) => setData(result))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(describeError(err, "Could not load usage."));
      });
    return () => controller.abort();
  }, [period]);

  return (
    <div className="grid gap-4">
      <label className="flex max-w-xs items-center gap-2 text-sm">
        <span className="text-muted-foreground">Month</span>
        <NativeSelect
          aria-label="Month"
          value={period}
          onChange={(event) => onPeriod(event.target.value)}
        >
          {options.map((value) => (
            <option key={value} value={value}>
              {formatPeriod(value)}
            </option>
          ))}
        </NativeSelect>
      </label>

      {error ? <ErrorNote message={error} /> : null}
      {!error && !data ? <EmptyNote>Loading usage…</EmptyNote> : null}

      {data ? (
        <>
          <dl
            className="grid gap-3 sm:grid-cols-4"
            data-testid="usage-totals"
            data-period={data.period}
          >
            {[
              ["LLM cost", formatUsd(data.total_cost_microusd)],
              ["Tokens in", formatTokens(data.total_tokens_in)],
              ["Tokens out", formatTokens(data.total_tokens_out)],
              ["Agent runs", String(data.total_agent_runs)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border p-3">
                <dt className="text-xs tracking-wide uppercase text-muted-foreground">{label}</dt>
                <dd className="mt-1 text-lg font-medium tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>

          <section aria-label="LLM cost by tenant" className="rounded-xl border p-3">
            <h3 className="mb-2 text-sm font-medium">
              LLM cost by tenant · {formatPeriod(data.period)}
            </h3>
            <CostChart page={data} />
          </section>

          <div className="rounded-xl border">
            <Table data-testid="usage-table">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {["Tenant", "Plan", "Region", "Tokens in", "Tokens out", "LLM cost", "Runs"].map(
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
                {data.items.map((row) => (
                  <TableRow key={row.tenant_id} data-testid="usage-row" data-slug={row.slug}>
                    <TableCell className="px-3 py-2.5 align-top">
                      <span className="flex flex-col leading-tight">
                        <span className="font-medium">{row.name}</span>
                        <span className="text-xs text-muted-foreground">{row.slug}</span>
                      </span>
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top">{row.plan}</TableCell>
                    <TableCell className="px-3 py-2.5 align-top uppercase">{row.region}</TableCell>
                    <TableCell className="px-3 py-2.5 align-top tabular-nums">
                      {formatTokens(row.tokens_in)}
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top tabular-nums">
                      {formatTokens(row.tokens_out)}
                    </TableCell>
                    <TableCell
                      className="px-3 py-2.5 align-top tabular-nums"
                      data-testid="usage-cost"
                    >
                      {formatUsd(row.cost_microusd)}
                    </TableCell>
                    <TableCell className="px-3 py-2.5 align-top tabular-nums">
                      {row.agent_runs}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      ) : null}
    </div>
  );
}
