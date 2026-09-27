"use client";

import { ArrowRightIcon } from "lucide-react";

import { DueTime } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { VersionOut } from "@/lib/opportunities/api";
import { humanizeKey } from "@/lib/opportunities/eligibility";
import { changeKindLabel, formatDiffValue } from "@/lib/opportunities/format";
import { cn } from "@/lib/utils";

const KIND_CLASS: Record<string, string> = {
  deadline_moved: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  new_attachment: "border-transparent bg-secondary text-secondary-foreground",
  qa_posted: "border-transparent bg-secondary text-secondary-foreground",
  cancelled: "border-transparent bg-destructive/10 text-destructive",
  awarded: "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  description_updated: "border-transparent bg-muted text-muted-foreground",
  status_changed: "border-transparent bg-muted text-muted-foreground",
  other: "border-transparent bg-muted text-muted-foreground",
};

const DATE_FIELDS = new Set(["response_due_at", "questions_due_at", "opening_at", "prebid_meeting_at", "posted_at", "archive_at"]);

function DiffValue({ field, value, sourceTz }: { field: string; value: unknown; sourceTz: string }) {
  if (DATE_FIELDS.has(field) && typeof value === "string" && value) {
    return <DueTime value={value} sourceTz={sourceTz} withYear fallback={<span>{value}</span>} />;
  }
  return <span className="break-words">{formatDiffValue(value)}</span>;
}

type DiffEntry = { old?: unknown; new?: unknown };

export function VersionsPanel({
  versions,
  currentVersion,
  sourceTz,
  className,
}: {
  versions: VersionOut[];
  currentVersion: number;
  sourceTz: string;
  className?: string;
}) {
  const ordered = [...versions].sort((a, b) => b.version - a.version);
  return (
    <Card data-testid="versions-card" className={className}>
      <CardHeader>
        <CardTitle>Versions</CardTitle>
        <CardDescription>
          {ordered.length
            ? `Version ${currentVersion} is current. Every re-read that changed the record is kept with a field-level diff.`
            : "No changes recorded yet; the first re-read that differs will appear here."}
        </CardDescription>
      </CardHeader>
      {ordered.length ? (
        <CardContent>
          <ol className="grid gap-4">
            {ordered.map((version) => {
              const entries = Object.entries((version.diff ?? {}) as Record<string, DiffEntry>);
              return (
                <li key={version.version} className="grid gap-2 rounded-lg border p-3" data-testid="version-row" data-version={version.version}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">Version {version.version}</span>
                    {version.version === currentVersion ? (
                      <Badge variant="secondary" className="font-normal">
                        current
                      </Badge>
                    ) : null}
                    <span className="text-xs text-muted-foreground">
                      <DueTime value={version.created_at} sourceTz={sourceTz} withYear />
                    </span>
                    <span className="ml-auto flex flex-wrap gap-1">
                      {version.changes.map((kind) => (
                        <Badge key={kind} variant="outline" className={cn(KIND_CLASS[kind] ?? "")} data-change-kind={kind}>
                          {changeKindLabel(kind)}
                        </Badge>
                      ))}
                    </span>
                  </div>
                  {entries.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Initial capture — no earlier version to compare.</p>
                  ) : (
                    <dl className="grid gap-1.5 text-sm">
                      {entries.map(([field, change]) => (
                        <div key={field} className="grid gap-x-3 gap-y-0.5 sm:grid-cols-[10rem_minmax(0,1fr)]" data-diff-field={field}>
                          <dt className="font-medium text-muted-foreground">{humanizeKey(field)}</dt>
                          <dd className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            <span className="text-muted-foreground line-through decoration-muted-foreground/60">
                              <DiffValue field={field} value={change?.old} sourceTz={sourceTz} />
                            </span>
                            <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                            <span className="sr-only">changed to</span>
                            <span>
                              <DiffValue field={field} value={change?.new} sourceTz={sourceTz} />
                            </span>
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </li>
              );
            })}
          </ol>
        </CardContent>
      ) : null}
    </Card>
  );
}
