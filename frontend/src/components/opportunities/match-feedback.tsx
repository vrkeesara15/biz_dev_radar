"use client";

import { ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import {
  ApiError,
  FEEDBACK_UNAVAILABLE_MESSAGE,
  NotAvailableError,
  sendMatchFeedback,
  type FeedbackThumb,
} from "@/lib/opportunities/api";
import { cn } from "@/lib/utils";

/** The "Not relevant because…" shortlist; "other" falls back to free text. */
export const NOT_RELEVANT_REASONS = [
  { value: "wrong_work", label: "Not the work we do" },
  { value: "wrong_geography", label: "Wrong geography" },
  { value: "too_small", label: "Value too small" },
  { value: "too_large", label: "Value too large" },
  { value: "not_eligible", label: "We are not eligible" },
  { value: "incumbent", label: "Incumbent advantage is too strong" },
  { value: "no_capacity", label: "No capacity before the deadline" },
  { value: "other", label: "Something else" },
] as const;

export type MatchFeedbackProps = {
  opportunityId: string;
  /** Short label used in the dialog and in the button's accessible name. */
  title?: string;
  size?: "xs" | "sm";
  className?: string;
};

/**
 * Thumbs up / down with a reason dialog (SPEC 6 learning loop, stored as
 * match_feedback). The route lands with M4-08; until then a 404 is reported as
 * "not available yet" rather than swallowed.
 */
export function MatchFeedback({ opportunityId, title, size = "xs", className }: MatchFeedbackProps) {
  const [given, setGiven] = React.useState<FeedbackThumb | null>(null);
  const [open, setOpen] = React.useState(false);
  const [reasonKey, setReasonKey] = React.useState<string>(NOT_RELEVANT_REASONS[0].value);
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const send = async (thumb: FeedbackThumb, reason?: string) => {
    setBusy(true);
    try {
      await sendMatchFeedback(opportunityId, { thumb, reason: reason ?? null });
      setGiven(thumb);
      setOpen(false);
      toast.success(thumb === "up" ? "Thanks — more like this" : "Thanks — we will show fewer of these");
    } catch (caught) {
      if (caught instanceof NotAvailableError) {
        setOpen(false);
        toast.info(FEEDBACK_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(
          caught instanceof ApiError
            ? errorMessage(caught.body, `Could not record the feedback (${caught.status})`)
            : "Could not record the feedback",
        );
      }
    } finally {
      setBusy(false);
    }
  };

  const submitReason = (event: React.FormEvent) => {
    event.preventDefault();
    const label = NOT_RELEVANT_REASONS.find((entry) => entry.value === reasonKey)?.label ?? reasonKey;
    const reason = reasonKey === "other" ? note.trim() : [label, note.trim()].filter(Boolean).join(" — ");
    if (!reason) return;
    void send("down", reason);
  };

  const suffix = title ? ` for “${title}”` : "";

  return (
    <div className={cn("flex items-center gap-1", className)} data-testid="match-feedback" data-given={given ?? ""}>
      <Button
        type="button"
        variant={given === "up" ? "secondary" : "ghost"}
        size={size === "xs" ? "icon-xs" : "icon-sm"}
        aria-label={`Relevant${suffix}`}
        aria-pressed={given === "up"}
        disabled={busy}
        data-testid="thumb-up"
        onClick={() => void send("up")}
      >
        <ThumbsUpIcon aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant={given === "down" ? "secondary" : "ghost"}
        size={size === "xs" ? "icon-xs" : "icon-sm"}
        aria-label={`Not relevant${suffix}`}
        aria-pressed={given === "down"}
        disabled={busy}
        data-testid="thumb-down"
        onClick={() => setOpen(true)}
      >
        <ThumbsDownIcon aria-hidden="true" />
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="not-relevant-dialog">
          <form onSubmit={submitReason} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Not relevant because…</DialogTitle>
              <DialogDescription>
                The reason tunes your keyword weights; nothing is applied to your profile without
                your approval.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="feedback-reason">Reason</Label>
              <NativeSelect
                id="feedback-reason"
                value={reasonKey}
                onChange={(event) => setReasonKey(event.target.value)}
              >
                {NOT_RELEVANT_REASONS.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="feedback-note">
                {reasonKey === "other" ? "Tell us more" : "Anything to add? (optional)"}
              </Label>
              <Textarea
                id="feedback-note"
                rows={3}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                required={reasonKey === "other"}
                placeholder="e.g. we only bid federal work above $1M"
              />
            </div>
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
              <Button type="submit" disabled={busy || (reasonKey === "other" && !note.trim())}>
                {busy ? "Sending…" : "Send feedback"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
