"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  browserTimeZone,
  countdown,
  countdownTone,
  dualTz,
  type CountdownTone,
  type TzDateInput,
} from "@/lib/opportunities/dates";

/** A clock that ticks once a minute so countdown badges stay honest. */
export function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = React.useState(() => new Date());
  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** The viewer's zone, resolved after mount so server and client markup agree. */
export function useUserTimeZone(): string | null {
  const [tz, setTz] = React.useState<string | null>(null);
  React.useEffect(() => setTz(browserTimeZone()), []);
  return tz;
}

const TONE_CLASS: Record<CountdownTone, string> = {
  overdue: "border-transparent bg-muted text-muted-foreground line-through decoration-muted-foreground/60",
  urgent: "border-transparent bg-destructive/10 text-destructive",
  soon: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  normal: "border-transparent bg-secondary text-secondary-foreground",
};

export function CountdownBadge({ due, now, className }: { due: Date; now: Date; className?: string }) {
  const text = countdown(now, due);
  const tone = countdownTone(now, due);
  return (
    <Badge
      variant="outline"
      className={cn("tabular-nums", TONE_CLASS[tone], className)}
      aria-label={text === "due now" || text.startsWith("overdue") ? text : `due in ${text}`}
      data-tone={tone}
    >
      {text}
    </Badge>
  );
}

export type DueTimeProps = {
  value: TzDateInput;
  /** IANA zone of the buyer/source; used when the API sends a plain ISO string. */
  sourceTz?: string | null;
  now?: Date;
  showCountdown?: boolean;
  withYear?: boolean;
  className?: string;
  /** Rendered when the value is missing. */
  fallback?: React.ReactNode;
};

/**
 * "Oct 14, 2:00 PM EDT = 11:30 PM IST" with an optional countdown badge. The
 * buyer's zone leads (that is the legally binding clock) and the user's zone
 * follows, per SPEC 5.3 / 9.
 */
export function DueTime({
  value,
  sourceTz,
  now,
  showCountdown = false,
  withYear = false,
  className,
  fallback = <span className="text-muted-foreground">—</span>,
}: DueTimeProps) {
  const userTz = useUserTimeZone();
  const dual = React.useMemo(() => dualTz(value, sourceTz, userTz, { withYear }), [value, sourceTz, userTz, withYear]);
  if (!dual) return <>{fallback}</>;
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-2 gap-y-1", className)}>
      <time
        dateTime={dual.utc.toISOString()}
        title={`${dual.buyerTz}${dual.userTz ? ` · your zone ${dual.userTz}` : ""}`}
        className="whitespace-nowrap tabular-nums"
      >
        <span>{dual.buyer}</span>
        {dual.user ? (
          <>
            <span aria-hidden="true" className="px-1 text-muted-foreground">
              =
            </span>
            <span className="text-muted-foreground">{dual.user}</span>
          </>
        ) : null}
      </time>
      {showCountdown ? <CountdownBadge due={dual.utc} now={now ?? new Date()} /> : null}
    </span>
  );
}
