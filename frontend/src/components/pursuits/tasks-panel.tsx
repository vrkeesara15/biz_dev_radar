"use client";

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import { createTask, listTasks, patchTask, type PursuitTask } from "@/lib/pursuits/api";
import { listMembers, type Member } from "@/lib/settings/api";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

const TASK_DONE = "done";

export type TasksPanelProps = { pursuitId: string; className?: string };

/**
 * Tasks on one pursuit (M6-07): create, complete and assign. Closing a task is
 * open to every role for a task assigned to you (OQ-118), so the checkbox is
 * never hidden — the server decides and a refusal becomes a toast.
 * Exported for the M5-18 pursuit workspace to embed.
 */
export function TasksPanel({ pursuitId, className }: TasksPanelProps) {
  const [tasks, setTasks] = React.useState<PursuitTask[] | null>(null);
  const [members, setMembers] = React.useState<Member[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [title, setTitle] = React.useState("");
  const [assignee, setAssignee] = React.useState("");
  const [dueAt, setDueAt] = React.useState("");

  React.useEffect(() => {
    const controller = new AbortController();
    listTasks(pursuitId, controller.signal)
      .then((list) => setTasks(list.items))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "Tasks could not be read"));
      });
    listMembers(controller.signal)
      .then(setMembers)
      .catch(() => setMembers([]));
    return () => controller.abort();
  }, [pursuitId]);

  const personLabel = React.useCallback(
    (userId: string | null) => {
      if (!userId) return "Unassigned";
      const member = members.find((row) => row.user_id === userId);
      return member?.name ?? member?.email ?? userId;
    },
    [members],
  );

  const replace = (row: PursuitTask) =>
    setTasks((current) => (current ?? []).map((item) => (item.id === row.id ? row : item)));

  const add = async (event: React.FormEvent) => {
    event.preventDefault();
    const clean = title.trim();
    if (!clean) return;
    setBusy("new");
    try {
      const created = await createTask(pursuitId, {
        title: clean,
        assignee_user_id: assignee || null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });
      setTasks((current) => [...(current ?? []), created]);
      setTitle("");
      setDueAt("");
    } catch (caught) {
      toast.error(describe(caught, "Could not create that task"));
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (row: PursuitTask, done: boolean) => {
    setBusy(row.id);
    try {
      replace(await patchTask(pursuitId, row.id, { status: done ? TASK_DONE : "open" }));
    } catch (caught) {
      toast.error(describe(caught, "Could not change that task"));
    } finally {
      setBusy(null);
    }
  };

  const reassign = async (row: PursuitTask, userId: string) => {
    setBusy(row.id);
    try {
      replace(await patchTask(pursuitId, row.id, { assignee_user_id: userId || null }));
    } catch (caught) {
      toast.error(describe(caught, "Could not reassign that task"));
    } finally {
      setBusy(null);
    }
  };

  const open = (tasks ?? []).filter((row) => row.status !== TASK_DONE).length;

  return (
    <Card data-testid="tasks-panel" className={className}>
      <CardHeader>
        <CardTitle>Tasks</CardTitle>
        <CardDescription>
          {tasks === null ? "Loading…" : `${open} open of ${tasks.length}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        {tasks && tasks.length ? (
          <ul className="grid divide-y" data-testid="tasks-list">
            {tasks.map((row) => {
              const done = row.status === TASK_DONE;
              return (
                <li key={row.id} data-testid="task-row" className="flex flex-wrap items-center gap-2 py-2 first:pt-0 last:pb-0">
                  <Checkbox
                    id={`task-${row.id}`}
                    checked={done}
                    disabled={busy === row.id}
                    onChange={(event) => void toggle(row, event.target.checked)}
                  />
                  <label
                    htmlFor={`task-${row.id}`}
                    className={`min-w-0 flex-1 text-sm ${done ? "text-muted-foreground line-through" : ""}`}
                  >
                    {row.title}
                  </label>
                  {row.source !== "user" ? <Badge variant="outline">{row.source}</Badge> : null}
                  <NativeSelect
                    className="w-40"
                    aria-label={`Assignee for ${row.title}`}
                    value={row.assignee_user_id ?? ""}
                    disabled={busy === row.id}
                    onChange={(event) => void reassign(row, event.target.value)}
                  >
                    <option value="">Unassigned</option>
                    {members.map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.name ?? member.email}
                      </option>
                    ))}
                    {row.assignee_user_id && !members.some((m) => m.user_id === row.assignee_user_id) ? (
                      <option value={row.assignee_user_id}>{personLabel(row.assignee_user_id)}</option>
                    ) : null}
                  </NativeSelect>
                </li>
              );
            })}
          </ul>
        ) : null}

        {tasks && tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground">No tasks yet.</p>
        ) : null}

        <form onSubmit={add} aria-label="Add a task" className="grid gap-2 border-t pt-3 sm:grid-cols-[minmax(0,1fr)_10rem_10rem_auto]">
          <div className="grid gap-1.5">
            <Label htmlFor="new-task-title">New task</Label>
            <Input
              id="new-task-title"
              value={title}
              placeholder="What needs doing?"
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-task-assignee">Assign to</Label>
            <NativeSelect
              id="new-task-assignee"
              value={assignee}
              onChange={(event) => setAssignee(event.target.value)}
            >
              <option value="">Unassigned</option>
              {members.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.name ?? member.email}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-task-due">Due</Label>
            <Input
              id="new-task-due"
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" size="sm" disabled={busy === "new" || !title.trim()}>
              Add task
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
