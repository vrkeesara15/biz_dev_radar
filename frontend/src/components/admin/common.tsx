"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/api/browser";
import { AdminApiError } from "@/lib/admin/api";
import { healthLabel, healthTone } from "@/lib/admin/format";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<string, string> = {
  ok: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  warn: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  bad: "bg-destructive/10 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

/** Health / run status pill. `data-tone` is what the e2e specs assert on. */
export function StatusBadge({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  const tone = healthTone(status);
  return (
    <Badge
      variant="ghost"
      data-tone={tone}
      data-status={status ?? "unknown"}
      className={cn(TONE_CLASS[tone], className)}
    >
      {healthLabel(status)}
    </Badge>
  );
}

/** Turns any thrown value into the sentence the console shows. */
export function describeError(error: unknown, fallback = "The request failed."): string {
  if (error instanceof AdminApiError) {
    if (error.status === 403) {
      return "Your account is not a platform admin, so the console cannot read this.";
    }
    return errorMessage(error.body, `${fallback} (${error.status})`);
  }
  return fallback;
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" data-testid="admin-error" className="text-sm text-destructive">
      {message}
    </p>
  );
}

export function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-sm text-muted-foreground">{children}</p>;
}

export type PagerProps = {
  page: number;
  pages: number;
  total: number;
  onPage: (page: number) => void;
  label: string;
  className?: string;
};

export function Pager({ page, pages, total, onPage, label, className }: PagerProps) {
  return (
    <nav
      aria-label={label}
      className={cn("flex flex-wrap items-center justify-between gap-3 text-sm", className)}
    >
      <p className="text-muted-foreground" aria-live="polite">
        {total.toLocaleString()} total
      </p>
      <div className="flex items-center gap-3">
        <span className="tabular-nums text-muted-foreground">
          Page {page} of {Math.max(pages, 1)}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
