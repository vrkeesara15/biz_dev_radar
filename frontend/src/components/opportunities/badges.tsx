import { Badge } from "@/components/ui/badge";
import { scoreBand } from "@/lib/opportunities/api";
import {
  NOTICE_TYPE_LABELS,
  STATUS_LABELS,
  type NoticeType,
  type OpportunityStatus,
} from "@/lib/opportunities/filters";
import { cn } from "@/lib/utils";

export function NoticeTypeBadge({ type, className }: { type: string; className?: string }) {
  const label = NOTICE_TYPE_LABELS[type as NoticeType] ?? type.replace(/_/g, " ");
  return (
    <Badge variant="outline" className={cn("uppercase tracking-wide", className)} data-notice-type={type}>
      {label}
    </Badge>
  );
}

const STATUS_CLASS: Record<OpportunityStatus, string> = {
  open: "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  closing_soon: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  closed: "border-transparent bg-muted text-muted-foreground",
  cancelled: "border-transparent bg-destructive/10 text-destructive",
  awarded: "border-transparent bg-secondary text-secondary-foreground",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const known = status as OpportunityStatus;
  return (
    <Badge variant="outline" className={cn(STATUS_CLASS[known] ?? "", className)} data-status={status}>
      {STATUS_LABELS[known] ?? status.replace(/_/g, " ")}
    </Badge>
  );
}

const BAND_CLASS = {
  high: "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  medium: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  low: "border-transparent bg-muted text-muted-foreground",
} as const;

/** Fit score 0–100 with SPEC 6 bands; "—" until a profile has been scored. */
export function ScoreBadge({ score, className }: { score: number | null | undefined; className?: string }) {
  const value = score === null || score === undefined ? null : Math.round(score);
  const band = scoreBand(value);
  if (value === null || !band) {
    return (
      <span className={cn("text-muted-foreground tabular-nums", className)} aria-label="Not scored yet" title="Not scored yet">
        —
      </span>
    );
  }
  return (
    <Badge variant="outline" className={cn("min-w-9 justify-center tabular-nums", BAND_CLASS[band], className)} data-band={band}>
      {value}
    </Badge>
  );
}

export function RegionBadge({ region, className }: { region: string; className?: string }) {
  return (
    <Badge variant="outline" className={cn("uppercase", className)}>
      {region}
    </Badge>
  );
}
