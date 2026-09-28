"use client";

import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { PursuitHeader } from "@/components/pursuits/pursuit-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import type { PursuitOut } from "@/lib/pursuits/api";
import { canApproveBudget, canApprovePackage, canExport, canRunAgents } from "@/lib/pursuits/roles";
import {
  AGENT_STEPS,
  costMeter,
  gateLabel,
  needsBudgetApproval,
  runStatusLabel,
  usd,
  type AgentStep,
} from "@/lib/pursuits/workspace";
import {
  approveBudget,
  approvePackage,
  markFinal,
  EXPORT_FORMATS,
  EXPORT_LABELS,
  runAgents,
  type Export,
  type ExportFormat,
} from "@/lib/pursuits/workspace-api";
import type { Role } from "@/types/next-auth";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

export function WorkspaceHeader({
  pursuit,
  role,
  title,
  buyer,
  ownerName,
  exports,
  packageFinal,
  exporting,
  onExport,
  onPursuit,
  onExportsChanged,
}: {
  pursuit: PursuitOut;
  role: Role | undefined;
  title: string | null;
  buyer: string | null;
  ownerName: string | null;
  exports: readonly Export[];
  packageFinal: boolean;
  exporting: ExportFormat | null;
  onExport: (format: ExportFormat) => void;
  onPursuit: (pursuit: PursuitOut) => void;
  onExportsChanged: () => void;
}) {
  const [step, setStep] = React.useState<AgentStep>("all");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [budgetOpen, setBudgetOpen] = React.useState(false);
  const [additional, setAdditional] = React.useState("10");
  const [reason, setReason] = React.useState("");
  const [gate2Note, setGate2Note] = React.useState("");
  const [gate2Error, setGate2Error] = React.useState<string | null>(null);

  const meter = costMeter(pursuit);
  const run = pursuit.run ?? null;
  const mayRun = canRunAgents(role);
  const mayApprove = canApprovePackage(role);
  const mayDownload = canExport(role);
  const approved = !!pursuit.package_approved_at;

  const start = async (which: AgentStep) => {
    setBusy("run");
    try {
      const result = await runAgents(pursuit.id, which);
      onPursuit(result.pursuit);
      toast.success(
        result.mode === "queued"
          ? `Queued ${result.steps.join(", ") || which}`
          : `Ran ${result.steps.join(", ") || which}`,
      );
    } catch (caught) {
      toast.error(describe(caught, "The agents could not be started"));
    } finally {
      setBusy(null);
    }
  };

  const raiseBudget = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("budget");
    try {
      const result = await approveBudget(pursuit.id, additional, reason);
      onPursuit(result.pursuit);
      setBudgetOpen(false);
      setReason("");
      toast.success(`Cap raised to ${result.new_cap_usd}`);
    } catch (caught) {
      toast.error(describe(caught, "That budget approval was refused"));
    } finally {
      setBusy(null);
    }
  };

  const approveThePackage = async () => {
    setBusy("gate2");
    setGate2Error(null);
    try {
      const result = await approvePackage(pursuit.id, gate2Note);
      onPursuit(result.pursuit);
      toast.success(`Package approved · ${result.approved_sections} sections`);
    } catch (caught) {
      const message = describe(caught, "The package could not be approved");
      setGate2Error(message);
      toast.error(message);
    } finally {
      setBusy(null);
    }
  };

  const finalise = async () => {
    setBusy("final");
    try {
      const result = await markFinal(pursuit.id, gate2Note);
      onExportsChanged();
      toast.success(
        result.package_final ? "Marked final: exports drop the internal footer" : "Marked final",
      );
    } catch (caught) {
      toast.error(describe(caught, "The package could not be marked final"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card data-testid="workspace-header">
      <CardContent className="grid gap-4 pt-5">
        <div>
          <Link
            href="/app/pipeline"
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            ← Pipeline
          </Link>
          <h1 className="mt-1 text-xl font-semibold tracking-tight" data-testid="workspace-title">
            {title ?? "Pursuit"}
          </h1>
          {buyer ? <p className="text-sm text-muted-foreground">{buyer}</p> : null}
        </div>

        <PursuitHeader pursuit={pursuit} ownerName={ownerName} />

        <div className="grid gap-4 border-t pt-4 md:grid-cols-2">
          {/* cost meter (SPEC 8 cost guard) */}
          <div data-testid="cost-meter">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">Agent cost</span>
              <span className="tabular-nums" data-testid="cost-values">
                {usd(meter.spent)} of {usd(meter.cap)}
              </span>
            </div>
            <Progress
              value={meter.percent}
              label="Agent cost against this pursuit's cap"
              className={meter.near ? "mt-1 [&>div]:bg-destructive" : "mt-1"}
            />
            <p className="mt-1 text-xs text-muted-foreground">
              {meter.monthRemaining === null
                ? "No monthly budget set for this tenant."
                : `${usd(meter.monthRemaining)} left in this month's tenant budget.`}
              {meter.over ? " The per-pursuit cap is reached." : ""}
            </p>
            {needsBudgetApproval(run) && canApproveBudget(role) ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2"
                data-testid="approve-budget"
                onClick={() => setBudgetOpen(true)}
              >
                Approve more budget
              </Button>
            ) : null}
          </div>

          {/* run agents + run state */}
          <div className="grid content-start gap-2" data-testid="run-agents">
            <div className="flex flex-wrap items-center gap-2">
              <Label htmlFor="agent-step" className="sr-only">
                Agent step
              </Label>
              <NativeSelect
                id="agent-step"
                data-testid="agent-step"
                className="w-56"
                value={step}
                disabled={!mayRun}
                onChange={(event) => setStep(event.target.value as AgentStep)}
              >
                <option value="all">All steps</option>
                {AGENT_STEPS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </NativeSelect>
              <Button
                type="button"
                size="sm"
                data-testid="run-step"
                disabled={!mayRun || busy !== null}
                onClick={() => void start(step)}
              >
                Run {step === "all" ? "all" : "step"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="run-all"
                disabled={!mayRun || busy !== null}
                onClick={() => void start("all")}
              >
                Run all
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <Badge variant="secondary" data-testid="run-status">
                {runStatusLabel(run?.status)}
              </Badge>
              {run?.gate ? (
                <Badge variant="destructive" data-testid="run-gate">
                  {gateLabel(run.gate)}
                </Badge>
              ) : null}
              {run?.pause_reason ? (
                <span className="text-muted-foreground">{run.pause_reason}</span>
              ) : null}
            </div>
          </div>
        </div>

        {/* Gate 2 and the exports */}
        <div className="grid gap-3 border-t pt-4" data-testid="gate-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">Gate 2 — final package</span>
            {approved ? (
              <Badge data-testid="package-approved">
                Approved{" "}
                {pursuit.package_approved_at
                  ? new Date(pursuit.package_approved_at).toLocaleDateString()
                  : ""}
              </Badge>
            ) : (
              <Badge variant="outline">Not approved</Badge>
            )}
            {packageFinal ? <Badge data-testid="package-final">Final</Badge> : null}
          </div>

          {mayApprove ? (
            <div className="grid gap-2 sm:max-w-xl">
              <Label htmlFor="gate2-note" className="text-xs">
                Note (recorded with the approval)
              </Label>
              <Textarea
                id="gate2-note"
                rows={2}
                value={gate2Note}
                onChange={(event) => setGate2Note(event.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  data-testid="approve-package"
                  disabled={approved || busy !== null}
                  onClick={() => void approveThePackage()}
                >
                  Approve package
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="mark-final"
                  disabled={!approved || packageFinal || busy !== null}
                  onClick={() => void finalise()}
                >
                  Mark final
                </Button>
              </div>
              {gate2Error ? (
                <p role="alert" data-testid="gate-2-error" className="text-sm text-destructive">
                  {gate2Error}
                </p>
              ) : null}
              <p className="text-xs text-muted-foreground">
                Exports carry a &ldquo;DRAFT — internal&rdquo; footer until the package is marked
                final. BidRadar never submits anything.
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="gate-2-readonly">
              Approving the final package is the bid manager&apos;s and tenant owner&apos;s (SPEC 3).
            </p>
          )}

          {mayDownload ? (
            <div className="flex flex-wrap items-center gap-2" data-testid="export-buttons">
              <span className="text-sm">Export</span>
              {EXPORT_FORMATS.map((format) => (
                <Button
                  key={format}
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid={`export-${format}`}
                  disabled={exporting !== null}
                  onClick={() => onExport(format)}
                >
                  {exporting === format ? "Preparing…" : EXPORT_LABELS[format]}
                </Button>
              ))}
            </div>
          ) : null}

          {mayDownload && exports.length ? (
            <ul className="grid gap-1 text-xs" data-testid="exports-list">
              {exports.map((row) => (
                <li key={row.id} data-testid="export-row" className="flex flex-wrap items-center gap-2">
                  <a
                    href={row.url ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline underline-offset-4"
                  >
                    {row.file_name}
                  </a>
                  <Badge variant="outline">{row.format.toUpperCase()}</Badge>
                  {row.renderer ? <span className="text-muted-foreground">{row.renderer}</span> : null}
                  <Badge variant={row.final ? "default" : "secondary"}>
                    {row.final ? "final" : "draft"}
                  </Badge>
                  <span className="text-muted-foreground">
                    {new Date(row.created_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </CardContent>

      <Dialog open={budgetOpen} onOpenChange={(next) => setBudgetOpen(next)}>
        <DialogContent data-testid="budget-dialog">
          <form onSubmit={raiseBudget} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Approve more budget</DialogTitle>
              <DialogDescription>
                The cost guard stopped this run at {usd(meter.cap)}. Raising the cap resumes it.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="additional-usd">Additional USD</Label>
              <Input
                id="additional-usd"
                inputMode="decimal"
                value={additional}
                onChange={(event) => setAdditional(event.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="budget-reason">Reason</Label>
              <Textarea
                id="budget-reason"
                rows={2}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </div>
            <DialogFooter showCloseButton>
              <Button type="submit" size="sm" disabled={busy === "budget"}>
                Approve
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
