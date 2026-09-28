"use client";

import Link from "next/link";
import * as React from "react";
import { EyeIcon, GripVerticalIcon } from "lucide-react";

import { CountdownBadge } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { dualTz, toDate } from "@/lib/opportunities/dates";
import { formatValueRange } from "@/lib/opportunities/format";
import type { PursuitListItem } from "@/lib/pursuits/api";
import { gateBadges, initials } from "@/lib/pursuits/stages";
import { cn } from "@/lib/utils";

export type OwnerLookup = (userId: string | null) => { name: string | null; email: string | null } | null;

export function OwnerAvatar({
  userId,
  lookup,
  className,
}: {
  userId: string | null;
  lookup?: OwnerLookup;
  className?: string;
}) {
  const person = userId ? lookup?.(userId) ?? null : null;
  const name = person?.name ?? person?.email ?? null;
  const text = userId ? initials(person?.name ?? null, person?.email ?? userId) : "—";
  const label = userId ? `Owner ${name ?? userId}` : "Unassigned";
  return (
    <span
      aria-label={label}
      title={label}
      data-testid="owner-avatar"
      className={cn(
        "inline-flex size-6 shrink-0 items-center justify-center rounded-full text-[10px] font-medium",
        userId ? "bg-primary/10 text-primary" : "border border-dashed text-muted-foreground",
        className,
      )}
    >
      {text}
    </span>
  );
}

const GATE_TONE = {
  warning: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  danger: "border-transparent bg-destructive/10 text-destructive",
} as const;

export function GateBadges({ item }: { item: PursuitListItem }) {
  const badges = gateBadges(item);
  if (!badges.length) return null;
  return (
    <>
      {badges.map((badge) => (
        <Badge key={badge.key} variant="outline" className={GATE_TONE[badge.tone]} data-gate={badge.key}>
          {badge.label}
        </Badge>
      ))}
    </>
  );
}

export type PursuitCardProps = {
  item: PursuitListItem;
  now: Date;
  userTz: string | null;
  lookup?: OwnerLookup;
  /** dnd-kit handle props; omitted in the static (non-draggable) rendering. */
  dragHandleProps?: React.HTMLAttributes<HTMLElement> & Record<string, unknown>;
  dragging?: boolean;
  /** The DragOverlay clone: a visual copy, hidden from assistive tech and tests. */
  overlay?: boolean;
  className?: string;
};

/**
 * One card on the board: title, buyer, value, due (dual time zone + countdown),
 * owner initials, watch flag and the SPEC 9 gate badges.
 */
export function PursuitCard({
  item,
  now,
  userTz,
  lookup,
  dragHandleProps,
  dragging = false,
  overlay = false,
  className,
}: PursuitCardProps) {
  const value = formatValueRange(item);
  const due = item.response_due_at ?? item.internal_due_at;
  const dual = dualTz(due, item.source_tz, userTz);
  const dueDate = toDate(due);

  return (
    <article
      data-testid={overlay ? "pursuit-card-overlay" : "pursuit-card"}
      data-pursuit-id={overlay ? undefined : item.id}
      data-stage={overlay ? undefined : item.stage}
      aria-hidden={overlay || undefined}
      aria-label={overlay ? undefined : `${item.title} — ${item.buyer_org ?? "unknown buyer"}`}
      className={cn(
        "grid gap-2 rounded-lg border bg-card p-3 text-sm shadow-xs",
        dragging && "opacity-50 ring-2 ring-ring",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        {dragHandleProps ? (
          <button
            type="button"
            {...dragHandleProps}
            data-testid="drag-handle"
            aria-label={`Move “${item.title}” to another stage`}
            className="mt-0.5 cursor-grab rounded-sm text-muted-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <GripVerticalIcon aria-hidden="true" className="size-4" />
          </button>
        ) : null}
        <Link
          href={`/app/pursuits/${item.id}`}
          className="line-clamp-2 min-w-0 flex-1 font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {item.title}
        </Link>
        <OwnerAvatar userId={item.owner_user_id} lookup={lookup} />
      </div>

      <p className="truncate text-xs text-muted-foreground" title={item.buyer_org ?? undefined}>
        {item.buyer_org ?? "Buyer not stated"}
      </p>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="tabular-nums" data-testid="card-value">
          {value.primary ?? "—"}
        </span>
        {value.usd ? <span className="text-muted-foreground tabular-nums">≈ {value.usd}</span> : null}
        <span className="text-muted-foreground uppercase">{item.region}</span>
      </div>

      {dual ? (
        <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="card-due">
          <time dateTime={dual.utc.toISOString()} className="tabular-nums text-muted-foreground">
            {dual.display}
          </time>
          {dueDate ? <CountdownBadge due={dueDate} now={now} /> : null}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-1.5">
        <GateBadges item={item} />
        {item.watch ? (
          <Badge variant="secondary" data-testid="watch-flag" aria-label="Watched">
            <EyeIcon aria-hidden="true" /> Watching
          </Badge>
        ) : null}
      </div>
    </article>
  );
}
