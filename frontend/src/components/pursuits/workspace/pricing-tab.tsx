"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ExportFormat } from "@/lib/pursuits/workspace-api";

/** `agents/pricing.PricingOutput` as far as the UI reads it. */
export type PricingSummary = {
  file_name: string | null;
  currency: string | null;
  region: string | null;
  labor_rows: number;
  rate_card_rows: number;
  placeholder_cells: number;
  warnings: string[];
  version: number | null;
};

const asNumber = (value: unknown) => {
  const parsed = typeof value === "number" ? value : Number.parseFloat(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
};

/** Reads a pricing artifact payload (or its `data` envelope); null when absent. */
export function readPricing(payload: unknown): PricingSummary | null {
  if (!payload || typeof payload !== "object") return null;
  const envelope = payload as Record<string, unknown>;
  if (Array.isArray(envelope.items)) {
    for (const item of envelope.items) {
      const found = readPricing(item);
      if (found) return found;
    }
  }
  const data =
    envelope.data && typeof envelope.data === "object"
      ? (envelope.data as Record<string, unknown>)
      : envelope;
  if (!("labor_rows" in data) && !("placeholder_cells" in data) && !("storage_key" in data)) {
    return null;
  }
  return {
    file_name: typeof data.file_name === "string" ? data.file_name : null,
    currency: typeof data.currency === "string" ? data.currency : null,
    region: typeof data.region === "string" ? data.region : null,
    labor_rows: asNumber(data.labor_rows),
    rate_card_rows: asNumber(data.rate_card_rows),
    placeholder_cells: asNumber(data.placeholder_cells),
    warnings: Array.isArray(data.warnings) ? data.warnings.map(String) : [],
    version: typeof data.version === "number" ? data.version : null,
  };
}

export function PricingTab({
  pricing,
  mayExport,
  exporting,
  onExport,
}: {
  pricing: PricingSummary | null;
  mayExport: boolean;
  exporting: ExportFormat | null;
  onExport: (format: ExportFormat) => void;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="pricing-tab">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Pricing workbook</CardTitle>
          <CardDescription>
            Agent 7 builds a template from the tenant&apos;s rate card. It never invents a price: every
            cell it cannot fill is a <code>[NEEDS INPUT: price]</code> placeholder with a task behind it.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {pricing ? (
            <>
              <dl className="grid grid-cols-2 gap-3 text-sm" data-testid="pricing-summary">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Labour rows</dt>
                  <dd className="tabular-nums">{pricing.labor_rows}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Rate-card rows</dt>
                  <dd className="tabular-nums">{pricing.rate_card_rows}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Cells needing a price</dt>
                  <dd className="tabular-nums">{pricing.placeholder_cells}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Currency</dt>
                  <dd>{pricing.currency ?? "—"}</dd>
                </div>
              </dl>
              {pricing.warnings.length ? (
                <ul className="grid gap-1 text-sm text-amber-700 dark:text-amber-300">
                  {pricing.warnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              ) : null}
              {pricing.file_name ? (
                <p className="text-xs text-muted-foreground">{pricing.file_name}</p>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="pricing-missing">
              No pricing summary to show: the workbook is stored as a pursuit artifact and no API route
              serves stored artifacts yet. The XLSX export below carries the same sheets.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Download</CardTitle>
          <CardDescription>
            The XLSX export holds the compliance matrix and the pricing sheets.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            data-testid="pricing-export-xlsx"
            disabled={!mayExport || exporting !== null}
            onClick={() => onExport("xlsx")}
          >
            {exporting === "xlsx" ? "Preparing…" : "Export XLSX"}
          </Button>
          {mayExport ? null : (
            <Badge variant="outline">Your role cannot download proposal exports</Badge>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
