"use client";

import Link from "next/link";
import * as React from "react";

import { CommentsThread } from "@/components/pursuits/comments-thread";
import { KeyDatesPanel } from "@/components/pursuits/key-dates-panel";
import { PursuitHeader } from "@/components/pursuits/pursuit-header";
import { TasksPanel } from "@/components/pursuits/tasks-panel";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import { getPursuit, type PursuitOut } from "@/lib/pursuits/api";
import { listMembers, type Member } from "@/lib/settings/api";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

/**
 * The minimal pursuit shell (SPEC 10.4 screen 6 is M5-18's): header, key
 * dates, tasks and comments. The three panels are exported from
 * `@/components/pursuits` so the workspace can drop them into its own tabs
 * rather than reimplementing them.
 */
export function PursuitScreen({ pursuitId }: { pursuitId: string }) {
  const [pursuit, setPursuit] = React.useState<PursuitOut | null>(null);
  const [members, setMembers] = React.useState<Member[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    getPursuit(pursuitId, controller.signal)
      .then(setPursuit)
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "This pursuit could not be read"));
      });
    listMembers(controller.signal)
      .then(setMembers)
      .catch(() => setMembers([]));
    return () => controller.abort();
  }, [pursuitId]);

  const ownerName = React.useMemo(() => {
    if (!pursuit?.owner_user_id) return null;
    const member = members.find((row) => row.user_id === pursuit.owner_user_id);
    return member?.name ?? member?.email ?? null;
  }, [members, pursuit]);

  return (
    <div className="grid gap-5">
      <div>
        <Link
          href="/app/pipeline"
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          ← Pipeline
        </Link>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">Pursuit</h1>
        <p className="text-sm text-muted-foreground">
          Deadlines, tasks and the conversation. The full workspace — bid/no-bid, compliance matrix, drafts,
          pricing — arrives with the drafting milestone.
        </p>
      </div>

      {error ? (
        <Card role="alert" data-testid="pursuit-error">
          <CardHeader>
            <CardTitle>Could not load this pursuit</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent />
        </Card>
      ) : null}

      {pursuit ? (
        <Card>
          <CardContent className="pt-5">
            <PursuitHeader pursuit={pursuit} ownerName={ownerName} />
          </CardContent>
        </Card>
      ) : null}

      <KeyDatesPanel pursuitId={pursuitId} region={null} />
      <TasksPanel pursuitId={pursuitId} />
      <CommentsThread pursuitId={pursuitId} />
    </div>
  );
}
