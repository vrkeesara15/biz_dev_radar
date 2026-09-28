"use client";

import { ExternalLinkIcon } from "lucide-react";
import * as React from "react";

import { DISCLAIMER } from "@/components/opportunities/attribution-footer";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { dualTz } from "@/lib/opportunities/dates";
import type { ChecklistItem, Packet } from "@/lib/pursuits/workspace-api";

function ChecklistCard({ items }: { items: readonly ChecklistItem[] }) {
  const byCategory = React.useMemo(() => {
    const groups = new Map<string, ChecklistItem[]>();
    for (const item of items) {
      const key = item.category || "other";
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()];
  }, [items]);

  return (
    <Card data-testid="checklist-card">
      <CardHeader>
        <CardTitle className="text-sm">Submission checklist</CardTitle>
        <CardDescription>
          Built from the solicitation by agent 3. The API stores no done flag for a checklist item, so
          this list is read-only — track the work as tasks on the Tasks tab.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No checklist yet. Run the extract and matrix agents from the header.
          </p>
        ) : null}
        {byCategory.map(([category, rows]) => (
          <div key={category}>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">
              {category.replace(/_/g, " ")}
            </h3>
            <ul className="mt-1 grid gap-1.5">
              {rows.map((item) => (
                <li
                  key={item.key}
                  data-testid="checklist-item"
                  className="flex flex-wrap items-baseline gap-2 rounded-lg border p-2 text-sm"
                >
                  <span className="font-medium">{item.label}</span>
                  {item.required ? (
                    <Badge variant="destructive">Required</Badge>
                  ) : (
                    <Badge variant="outline">Optional</Badge>
                  )}
                  {item.note ? <span className="text-muted-foreground">{item.note}</span> : null}
                  {item.source_req_ids?.length ? (
                    <span className="font-mono text-xs text-muted-foreground">
                      {item.source_req_ids.join(", ")}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/**
 * SPEC 8's "submission packet" page: what to upload where, the portal link,
 * the signature/DSC steps and the deadline in both zones. Nothing here
 * submits anything, and nothing here ever will (SPEC 11).
 */
function PacketCard({ packet }: { packet: Packet }) {
  const deadline = dualTz(packet.packet.deadline ?? null, null);
  return (
    <Card data-testid="packet-card">
      <CardHeader>
        <CardTitle className="text-sm">Submission packet</CardTitle>
        <CardDescription>
          {packet.packet.submission_method
            ? `Submission method: ${packet.packet.submission_method}.`
            : "How this buyer takes the response."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Portal</dt>
            <dd>
              {packet.packet.portal_url ? (
                <a
                  href={packet.packet.portal_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="packet-portal"
                  className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"
                >
                  {packet.packet.portal ?? packet.packet.portal_url}
                  <ExternalLinkIcon className="size-3.5" aria-hidden="true" />
                </a>
              ) : (
                <span className="text-sm text-muted-foreground">{packet.packet.portal ?? "—"}</span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Final deadline</dt>
            <dd className="text-sm" data-testid="packet-deadline">
              {deadline ? (
                <time dateTime={deadline.utc.toISOString()} className="tabular-nums">
                  {deadline.display}
                </time>
              ) : (
                <span className="text-muted-foreground">Not stated</span>
              )}
            </dd>
          </div>
        </dl>

        {packet.packet.upload_steps?.length ? (
          <div>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">What to upload where</h3>
            <ol className="mt-1 grid gap-1.5">
              {packet.packet.upload_steps.map((step) => (
                <li key={step.order} data-testid="upload-step" className="rounded-lg border p-2 text-sm">
                  <span className="font-medium">
                    {step.order}. {step.label}
                  </span>
                  <span className="block text-muted-foreground">
                    {step.destination}
                    {step.file_name ? ` · ${step.file_name}` : ""}
                    {step.formats?.length ? ` · ${step.formats.join(", ")}` : ""}
                  </span>
                  {step.note ? <span className="block text-muted-foreground">{step.note}</span> : null}
                </li>
              ))}
            </ol>
          </div>
        ) : null}

        {packet.packet.signatures?.length ? (
          <div>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">Signatures</h3>
            <ul className="mt-1 grid gap-1.5 text-sm">
              {packet.packet.signatures.map((row) => (
                <li key={row.key} data-testid="signature-step" className="rounded-lg border p-2">
                  <span className="font-medium">{row.label}</span>
                  <span className="block text-muted-foreground">Signed by {row.signed_by}</span>
                  {row.note ? <span className="block text-muted-foreground">{row.note}</span> : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {packet.packet.dsc_steps?.length ? (
          <div>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">
              Digital signature certificate
            </h3>
            <ol className="mt-1 grid list-decimal gap-1 pl-5 text-sm">
              {packet.packet.dsc_steps.map((step, index) => (
                <li key={index} data-testid="dsc-step">
                  {step}
                </li>
              ))}
            </ol>
          </div>
        ) : null}

        {packet.packet.sam_login_note ? (
          <p className="text-sm text-muted-foreground" data-testid="sam-note">
            {packet.packet.sam_login_note}
          </p>
        ) : null}

        <p
          role="note"
          data-testid="packet-disclaimer"
          className="rounded-lg border border-amber-500/40 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
        >
          {packet.packet.disclaimer || `BidRadar never submits a bid. ${DISCLAIMER}`}
        </p>
      </CardContent>
    </Card>
  );
}

export function ChecklistTab({
  packet,
  checklist,
  error,
}: {
  packet: Packet | null;
  checklist: readonly ChecklistItem[];
  error: string | null;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="checklist-tab">
      {error ? (
        <p role="alert" className="text-sm text-destructive lg:col-span-2">
          {error}
        </p>
      ) : null}
      <ChecklistCard items={checklist} />
      {packet ? (
        <PacketCard packet={packet} />
      ) : (
        <Card data-testid="packet-missing">
          <CardHeader>
            <CardTitle className="text-sm">Submission packet</CardTitle>
            <CardDescription>
              The packet is built once the solicitation has been parsed and its requirements
              extracted.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  );
}
