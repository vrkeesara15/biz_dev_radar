"use client";

import * as React from "react";
import { toast } from "sonner";

import { useNow } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import { relativeTime } from "@/lib/notifications/api";
import { ApiError } from "@/lib/opportunities/api";
import { createComment, listComments, patchComment, type PursuitComment } from "@/lib/pursuits/api";
import { listMembers, type Member } from "@/lib/settings/api";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

export type CommentsThreadProps = {
  pursuitId: string;
  /** Anchors the thread to one artefact ("pursuit" by default). */
  targetType?: string;
  /** The row the thread hangs off, e.g. a draft id for `draft_section`. */
  targetId?: string | null;
  className?: string;
};

/**
 * The pursuit's comment thread (M6-07): post, read and resolve. Exported for
 * the M5-18 pursuit workspace, which anchors its own threads to a draft by
 * passing `targetType`.
 */
export function CommentsThread({
  pursuitId,
  targetType = "pursuit",
  targetId = null,
  className,
}: CommentsThreadProps) {
  const now = useNow();
  const [comments, setComments] = React.useState<PursuitComment[] | null>(null);
  const [members, setMembers] = React.useState<Member[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [body, setBody] = React.useState("");

  React.useEffect(() => {
    const controller = new AbortController();
    listComments(pursuitId, { targetType, targetId }, controller.signal)
      .then((list) => setComments(list.items))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "Comments could not be read"));
      });
    listMembers(controller.signal)
      .then(setMembers)
      .catch(() => setMembers([]));
    return () => controller.abort();
  }, [pursuitId, targetType, targetId]);

  const author = (userId: string | null) => {
    if (!userId) return "Someone";
    const member = members.find((row) => row.user_id === userId);
    return member?.name ?? member?.email ?? "Someone";
  };

  const post = async (event: React.FormEvent) => {
    event.preventDefault();
    const clean = body.trim();
    if (!clean) return;
    setBusy("new");
    try {
      const created = await createComment(pursuitId, {
        body: clean,
        target_type: targetType,
        target_id: targetId,
      });
      setComments((current) => [...(current ?? []), created]);
      setBody("");
    } catch (caught) {
      toast.error(describe(caught, "Could not post that comment"));
    } finally {
      setBusy(null);
    }
  };

  const resolve = async (row: PursuitComment, resolved: boolean) => {
    setBusy(row.id);
    try {
      const updated = await patchComment(pursuitId, row.id, { resolved });
      setComments((current) => (current ?? []).map((item) => (item.id === row.id ? updated : item)));
    } catch (caught) {
      toast.error(describe(caught, "Could not resolve that comment"));
    } finally {
      setBusy(null);
    }
  };

  const unresolved = (comments ?? []).filter((row) => !row.resolved_at).length;

  return (
    <Card data-testid="comments-thread" className={className}>
      <CardHeader>
        <CardTitle>Comments</CardTitle>
        <CardDescription>
          {comments === null ? "Loading…" : `${unresolved} unresolved of ${comments.length}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        {comments && comments.length ? (
          <ul className="grid gap-3" data-testid="comments-list">
            {comments.map((row) => (
              <li key={row.id} data-testid="comment-row" className="grid gap-1 rounded-lg border p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">{author(row.author_user_id)}</span>
                  <span>{relativeTime(row.created_at, now)}</span>
                  {row.resolved_at ? <Badge variant="secondary">Resolved</Badge> : null}
                </div>
                <p className="text-sm whitespace-pre-wrap">{row.body}</p>
                <div>
                  <Button
                    type="button"
                    size="xs"
                    variant="ghost"
                    disabled={busy === row.id}
                    onClick={() => void resolve(row, !row.resolved_at)}
                  >
                    {row.resolved_at ? "Reopen" : "Resolve"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}

        {comments && comments.length === 0 ? (
          <p className="text-sm text-muted-foreground">No comments yet.</p>
        ) : null}

        <form onSubmit={post} aria-label="Post a comment" className="grid gap-2 border-t pt-3">
          <Label htmlFor="new-comment">Add a comment</Label>
          <Textarea
            id="new-comment"
            rows={3}
            value={body}
            placeholder="Ask a question or leave a note for the team"
            onChange={(event) => setBody(event.target.value)}
          />
          <div>
            <Button type="submit" size="sm" disabled={busy === "new" || !body.trim()}>
              Post comment
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
