"use client";

import Link from "next/link";
import * as React from "react";

import { CountdownBadge, useNow, useUserTimeZone } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { dualTz } from "@/lib/opportunities/dates";
import type { PursuitOut } from "@/lib/pursuits/api";
import { initials, stageLabel } from "@/lib/pursuits/stages";

export type PursuitHeaderProps = {
  pursuit: PursuitOut;
  /** Resolves an owner id to a display name, when the member list is loaded. */
  ownerName?: string | null;
  className?: string;
};

/** Stage, owner and internal deadline for one pursuit (SPEC 9). */
export function PursuitHeader({ pursuit, ownerName, className }: PursuitHeaderProps) {
  const now = useNow();
  const userTz = useUserTimeZone();
  const internal = dualTz(pursuit.internal_due_at, null, userTz);

  return (
    <header className={className} data-testid="pursuit-header">
      <div className="flex flex-wrap items-center gap-2">
        <Badge data-testid="pursuit-stage">{stageLabel(pursuit.stage)}</Badge>
        {pursuit.decision ? <Badge variant="outline">Decision: {pursuit.decision}</Badge> : null}
        {pursuit.watch ? <Badge variant="secondary">Watching</Badge> : null}
        {pursuit.matrix_recheck_required ? (
          <Badge variant="destructive">Compliance re-check needed</Badge>
        ) : null}
      </div>
      <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">Owner</dt>
          <dd className="flex items-center gap-2" data-testid="pursuit-owner">
            {pursuit.owner_user_id ? (
              <>
                <span
                  aria-hidden="true"
                  className="inline-flex size-6 items-center justify-center rounded-full bg-primary/10 text-[10px] font-medium text-primary"
                >
                  {initials(ownerName ?? null, pursuit.owner_user_id)}
                </span>
                <span>{ownerName ?? pursuit.owner_user_id}</span>
              </>
            ) : (
              <span className="text-muted-foreground">Unassigned</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">Internal deadline</dt>
          <dd className="flex flex-wrap items-center gap-2" data-testid="pursuit-internal-due">
            {internal ? (
              <>
                <time dateTime={internal.utc.toISOString()} className="tabular-nums">
                  {internal.display}
                </time>
                <CountdownBadge due={internal.utc} now={now} />
              </>
            ) : (
              <span className="text-muted-foreground">Not set</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-muted-foreground">Notice</dt>
          <dd>
            <Link
              href={`/app/opportunities/${pursuit.opportunity_id}`}
              className="underline-offset-4 hover:underline"
            >
              Open the opportunity →
            </Link>
          </dd>
        </div>
      </dl>
    </header>
  );
}
