"use client";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import type { CompletenessLike } from "@/lib/completeness";
import { stepProgress } from "@/lib/completeness";
import { STEPS, type Region, type StepId } from "@/lib/profile-fields";
import { cn } from "@/lib/utils";

import { CompletenessMeter } from "./completeness-meter";

export function Stepper({
  current,
  region,
  completeness,
  pastPerformanceCount,
  canNavigate,
  onNavigate,
}: {
  current: StepId;
  region: Region;
  completeness: CompletenessLike | null;
  pastPerformanceCount?: number;
  /** Steps become navigable once a profile exists. */
  canNavigate: boolean;
  onNavigate: (step: StepId) => void;
}) {
  return (
    <header className="grid gap-4 border-b pb-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Set up your company profile</h1>
          <p className="text-sm text-muted-foreground">
            Step {current} of {STEPS.length}: {STEPS[current - 1].title}
          </p>
        </div>
        <Badge variant="outline" aria-label={`Profile region ${region === "IN" ? "India" : "United States"}`}>
          {region === "IN" ? "India" : "United States"}
        </Badge>
      </div>
      <Progress value={stepProgress(current)} label="Wizard progress" />
      <nav aria-label="Onboarding steps">
        <ol className="grid grid-cols-7 gap-1">
          {STEPS.map((s) => {
            const state = s.id === current ? "current" : s.id < current ? "done" : "todo";
            const clickable = canNavigate && s.id !== current;
            return (
              <li key={s.id}>
                <button
                  type="button"
                  disabled={!clickable}
                  aria-current={state === "current" ? "step" : undefined}
                  aria-label={`Step ${s.id}: ${s.title}`}
                  onClick={() => onNavigate(s.id)}
                  className={cn(
                    "flex w-full flex-col items-start gap-1 rounded-md px-2 py-1.5 text-left text-xs transition-colors disabled:cursor-default",
                    state === "current" && "bg-muted font-medium text-foreground",
                    state === "done" && "text-foreground hover:bg-muted",
                    state === "todo" && "text-muted-foreground",
                    clickable && "hover:bg-muted",
                  )}
                >
                  <span
                    className={cn(
                      "inline-flex size-5 items-center justify-center rounded-full border text-[11px] tabular-nums",
                      state !== "todo" && "border-foreground bg-foreground text-background",
                    )}
                    aria-hidden
                  >
                    {s.id}
                  </span>
                  <span className="truncate">{s.short}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
      {completeness ? (
        <CompletenessMeter
          completeness={completeness}
          pastPerformanceCount={pastPerformanceCount}
          onGoToStep={canNavigate ? onNavigate : undefined}
          compact
        />
      ) : (
        <p className="text-xs text-muted-foreground">
          Completeness appears after the profile is created at the end of step 1.
        </p>
      )}
    </header>
  );
}
