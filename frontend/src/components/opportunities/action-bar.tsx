"use client";

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
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import {
  NotAvailableError,
  PIPELINE_UNAVAILABLE_MESSAGE,
  pipelineAction,
  type PipelineAction,
} from "@/lib/opportunities/api";
import { cn } from "@/lib/utils";

const SUCCESS: Record<PipelineAction, string> = {
  pursue: "Added to your pipeline — the agent workflow starts now.",
  watch: "Watching. You will hear about amendments and deadline moves.",
  pass: "Passed. Thanks — the reason tunes future matching.",
};

/** SPEC 7 one-click actions: Pursue / Watch / Pass (with reason). POSTs the M6-01 routes. */
export function ActionBar({ opportunityId, className }: { opportunityId: string; className?: string }) {
  const [pending, setPending] = React.useState<PipelineAction | null>(null);
  const [passOpen, setPassOpen] = React.useState(false);
  const [reason, setReason] = React.useState("");

  const run = async (action: PipelineAction, body?: { reason?: string }) => {
    setPending(action);
    try {
      await pipelineAction(opportunityId, action, body);
      toast.success(SUCCESS[action]);
      return true;
    } catch (error) {
      if (error instanceof NotAvailableError) {
        toast.info(PIPELINE_UNAVAILABLE_MESSAGE);
      } else {
        toast.error(errorMessage((error as { body?: unknown })?.body, `Could not ${action} this opportunity`));
      }
      return false;
    } finally {
      setPending(null);
    }
  };

  const submitPass = async (event: React.FormEvent) => {
    event.preventDefault();
    const clean = reason.trim();
    if (!clean) return;
    const ok = await run("pass", { reason: clean });
    if (ok) {
      setPassOpen(false);
      setReason("");
    }
  };

  return (
    <div role="group" aria-label="Pipeline actions" data-testid="action-bar" className={cn("flex flex-wrap items-center gap-2", className)}>
      <Button type="button" onClick={() => run("pursue")} disabled={pending !== null}>
        {pending === "pursue" ? "Pursuing…" : "Pursue"}
      </Button>
      <Button type="button" variant="outline" onClick={() => run("watch")} disabled={pending !== null}>
        {pending === "watch" ? "Watching…" : "Watch"}
      </Button>
      <Button type="button" variant="ghost" onClick={() => setPassOpen(true)} disabled={pending !== null}>
        Pass
      </Button>

      <Dialog open={passOpen} onOpenChange={setPassOpen}>
        <DialogContent>
          <form onSubmit={submitPass} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Pass on this opportunity</DialogTitle>
              <DialogDescription>
                Tell us why. Reasons feed the weekly keyword re-tune; nothing is applied without your approval.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="pass-reason">Reason</Label>
              <Textarea
                id="pass-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="e.g. Set-aside we cannot meet; incumbent too entrenched; outside our geography"
                rows={4}
                required
                autoFocus
              />
            </div>
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
              <Button type="submit" variant="destructive" disabled={pending !== null || !reason.trim()}>
                {pending === "pass" ? "Passing…" : "Pass with reason"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
