"use client";

import * as React from "react";

import { CommentsThread } from "@/components/pursuits/comments-thread";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { relativeTime } from "@/lib/notifications/api";
import type { ActivityEvent } from "@/lib/pursuits/workspace";

const KIND_LABELS: Record<ActivityEvent["kind"], string> = {
  decision: "Gate 1",
  approval: "Gate 2",
  run: "Agents",
  draft: "Draft",
  task: "Task",
  comment: "Comment",
  export: "Export",
  pursuit: "Pursuit",
};

export function ActivityTab({
  events,
  pursuitId,
  now,
  ownerName,
  canComment,
}: {
  events: readonly ActivityEvent[];
  pursuitId: string;
  now: Date;
  ownerName: (userId: string | null) => string;
  canComment: boolean;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]" data-testid="activity-tab">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Activity</CardTitle>
          <CardDescription>
            Composed from the pursuit, its agent run, drafts, tasks, comments and exports — the API
            exposes no per-tenant audit feed, so this is what those records timestamp.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing has happened yet.</p>
          ) : (
            <ol className="grid gap-2" data-testid="activity-list">
              {events.map((event) => (
                <li
                  key={event.id}
                  data-testid="activity-row"
                  data-kind={event.kind}
                  className="grid gap-0.5 border-l-2 pl-3"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">{KIND_LABELS[event.kind]}</Badge>
                    <time dateTime={event.at}>{relativeTime(event.at, now)}</time>
                    {event.userId ? <span>{ownerName(event.userId)}</span> : null}
                  </div>
                  <p className="text-sm">{event.title}</p>
                  {event.detail ? (
                    <p className="text-xs text-muted-foreground">{event.detail}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {canComment ? <CommentsThread pursuitId={pursuitId} /> : null}
    </div>
  );
}
