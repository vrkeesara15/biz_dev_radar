"use client";

import * as React from "react";
import { toast } from "sonner";

import { CountdownBadge, useNow, useUserTimeZone } from "@/components/opportunities/due-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import { dualTz, toDate } from "@/lib/opportunities/dates";
import {
  acknowledgeKeyDate,
  createKeyDate,
  deleteKeyDate,
  listKeyDates,
  updateKeyDate,
  type KeyDate,
} from "@/lib/pursuits/api";
import {
  KEY_DATE_LABELS,
  addableKinds,
  isoToLocalInput,
  keyDateKindLabel,
  localInputToIso,
} from "@/lib/pursuits/key-dates";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

type DialogState =
  | { mode: "closed" }
  | { mode: "add" }
  | { mode: "edit"; date: KeyDate }
  | { mode: "delete"; date: KeyDate };

function KeyDateDialog({
  state,
  existing,
  region,
  onClose,
  onSubmit,
}: {
  state: DialogState;
  existing: KeyDate[];
  region: string | null | undefined;
  onClose: () => void;
  onSubmit: (values: { kind: string; at: string; label: string; note: string }) => Promise<void>;
}) {
  const editing = state.mode === "edit" ? state.date : null;
  const kinds = React.useMemo(() => addableKinds(existing, region), [existing, region]);
  const [kind, setKind] = React.useState<string>(editing?.kind ?? kinds[0] ?? "custom");
  const [at, setAt] = React.useState(isoToLocalInput(editing?.at.utc));
  const [label, setLabel] = React.useState(editing?.label ?? "");
  const [note, setNote] = React.useState(editing?.note ?? "");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (state.mode === "edit") {
      setKind(state.date.kind);
      setAt(isoToLocalInput(state.date.at.utc));
      setLabel(state.date.label);
      setNote(state.date.note ?? "");
    } else if (state.mode === "add") {
      setKind(kinds[0] ?? "custom");
      setAt("");
      setLabel("");
      setNote("");
    }
  }, [state, kinds]);

  const open = state.mode === "add" || state.mode === "edit";

  const submit = async () => {
    const iso = localInputToIso(at);
    if (!iso) {
      toast.error("Pick a date and time first.");
      return;
    }
    setBusy(true);
    try {
      await onSubmit({ kind, at: iso, label: label.trim(), note: note.trim() });
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent data-testid="key-date-dialog">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit key date" : "Add a key date"}</DialogTitle>
          <DialogDescription>
            Times are entered in your own zone and shown alongside the buyer&apos;s.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="key-date-kind">Kind</Label>
            <NativeSelect
              id="key-date-kind"
              value={kind}
              disabled={!!editing}
              onChange={(event) => setKind(event.target.value)}
            >
              {(editing ? [editing.kind] : kinds).map((value) => (
                <option key={value} value={value}>
                  {keyDateKindLabel(value)}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="key-date-at">When</Label>
            <Input
              id="key-date-at"
              type="datetime-local"
              value={at}
              onChange={(event) => setAt(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="key-date-label">Label</Label>
            <Input
              id="key-date-label"
              value={label}
              placeholder={KEY_DATE_LABELS.custom}
              onChange={(event) => setLabel(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="key-date-note">Note</Label>
            <Textarea
              id="key-date-note"
              rows={2}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={busy}>
            {editing ? "Save" : "Add date"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export type KeyDatesPanelProps = {
  pursuitId: string;
  /** "us" | "in" — decides whether the India-only kinds are offered. */
  region?: string | null;
  className?: string;
};

/**
 * Key dates for one pursuit (SPEC 9, M6-02): every date with its dual time
 * zone string and countdown, an Acknowledge button per row (which is what
 * stops the reminder ladder escalating, M6-03) and add / edit / delete
 * dialogs. Exported for the M5-18 pursuit workspace to embed.
 */
export function KeyDatesPanel({ pursuitId, region, className }: KeyDatesPanelProps) {
  const now = useNow();
  const userTz = useUserTimeZone();
  const [dates, setDates] = React.useState<KeyDate[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [dialog, setDialog] = React.useState<DialogState>({ mode: "closed" });

  React.useEffect(() => {
    const controller = new AbortController();
    listKeyDates(pursuitId, controller.signal)
      .then((list) => setDates(list.items))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(caught, "Key dates could not be read"));
      });
    return () => controller.abort();
  }, [pursuitId]);

  const replace = (row: KeyDate) =>
    setDates((current) => (current ?? []).map((item) => (item.id === row.id ? row : item)));

  const acknowledge = async (row: KeyDate) => {
    setBusy(row.id);
    try {
      replace(await acknowledgeKeyDate(pursuitId, row.id));
    } catch (caught) {
      toast.error(describe(caught, "Could not acknowledge that date"));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (row: KeyDate) => {
    setBusy(row.id);
    try {
      await deleteKeyDate(pursuitId, row.id);
      setDates((current) => (current ?? []).filter((item) => item.id !== row.id));
      setDialog({ mode: "closed" });
    } catch (caught) {
      toast.error(describe(caught, "Could not delete that date"));
    } finally {
      setBusy(null);
    }
  };

  const save = async (values: { kind: string; at: string; label: string; note: string }) => {
    const editing = dialog.mode === "edit" ? dialog.date : null;
    try {
      if (editing) {
        replace(
          await updateKeyDate(pursuitId, editing.id, {
            at: values.at,
            label: values.label || null,
            note: values.note || null,
          }),
        );
      } else {
        const created = await createKeyDate(pursuitId, {
          kind: values.kind,
          at: values.at,
          label: values.label || null,
          note: values.note || null,
        });
        setDates((current) => [...(current ?? []), created].sort((a, b) => a.at.utc.localeCompare(b.at.utc)));
      }
    } catch (caught) {
      toast.error(describe(caught, "Could not save that date"));
      throw caught;
    }
  };

  return (
    <Card data-testid="key-dates-panel" className={className}>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>Key dates</CardTitle>
          <CardDescription>
            Auto-created from the notice and editable. Acknowledge a date to stop it escalating.
          </CardDescription>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => setDialog({ mode: "add" })}>
          Add date
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3">
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
        {!dates && !error ? <p className="text-sm text-muted-foreground">Loading…</p> : null}
        {dates && dates.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No key dates yet. The notice has no response deadline to derive them from.
          </p>
        ) : null}
        {dates && dates.length ? (
          <ul className="grid divide-y" data-testid="key-dates-list">
            {dates.map((row) => {
              const dual = dualTz(row.at, row.at.buyer_tz, userTz);
              const when = toDate(row.at);
              return (
                <li key={row.id} data-testid="key-date-row" data-kind={row.kind} className="grid gap-1 py-2.5 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{row.label || keyDateKindLabel(row.kind)}</span>
                    <Badge variant="outline">{keyDateKindLabel(row.kind)}</Badge>
                    {row.source === "user" ? <Badge variant="secondary">Edited</Badge> : null}
                    {row.acknowledged_at ? (
                      <Badge variant="secondary" data-testid="acknowledged">
                        Acknowledged
                      </Badge>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <time dateTime={row.at.utc} className="tabular-nums text-muted-foreground">
                      {dual?.display ?? row.at.display}
                    </time>
                    {when ? <CountdownBadge due={when} now={now} /> : null}
                  </div>
                  {row.note ? <p className="text-xs text-muted-foreground">{row.note}</p> : null}
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button
                      type="button"
                      size="xs"
                      variant="outline"
                      disabled={busy === row.id || !!row.acknowledged_at}
                      onClick={() => void acknowledge(row)}
                    >
                      {row.acknowledged_at ? "Acknowledged" : "Acknowledge"}
                    </Button>
                    <Button
                      type="button"
                      size="xs"
                      variant="ghost"
                      onClick={() => setDialog({ mode: "edit", date: row })}
                      aria-label={`Edit ${row.label || keyDateKindLabel(row.kind)}`}
                    >
                      Edit
                    </Button>
                    <Button
                      type="button"
                      size="xs"
                      variant="ghost"
                      onClick={() => setDialog({ mode: "delete", date: row })}
                      aria-label={`Delete ${row.label || keyDateKindLabel(row.kind)}`}
                    >
                      Delete
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}
      </CardContent>

      <KeyDateDialog
        state={dialog}
        existing={dates ?? []}
        region={region}
        onClose={() => setDialog({ mode: "closed" })}
        onSubmit={save}
      />

      <Dialog
        open={dialog.mode === "delete"}
        onOpenChange={(next) => (next ? undefined : setDialog({ mode: "closed" }))}
      >
        <DialogContent data-testid="key-date-delete-dialog">
          <DialogHeader>
            <DialogTitle>Delete this key date?</DialogTitle>
            <DialogDescription>
              {dialog.mode === "delete"
                ? `“${dialog.date.label || keyDateKindLabel(dialog.date.kind)}” and its reminders are removed.`
                : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDialog({ mode: "closed" })}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => dialog.mode === "delete" && void remove(dialog.date)}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
