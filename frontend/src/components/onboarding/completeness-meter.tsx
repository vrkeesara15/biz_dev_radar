"use client";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  completenessBadges,
  topMissing,
  type CompletenessLike,
} from "@/lib/completeness";
import type { StepId } from "@/lib/profile-fields";
import { cn } from "@/lib/utils";

export function CompletenessMeter({
  completeness,
  pastPerformanceCount,
  onGoToStep,
  compact = false,
}: {
  completeness: CompletenessLike;
  pastPerformanceCount?: number;
  onGoToStep?: (step: StepId) => void;
  compact?: boolean;
}) {
  const badges = completenessBadges(completeness, pastPerformanceCount);
  const missing = topMissing(completeness.missing, compact ? 3 : 8);
  return (
    <div data-testid="completeness-meter" className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">
          Completeness{" "}
          <span data-testid="completeness-score" className="tabular-nums">
            {completeness.score}
          </span>
          <span className="text-muted-foreground">/100</span>
        </span>
        {badges.map((b) => (
          <Badge
            key={b.id}
            data-testid={`badge-${b.id}`}
            data-tone={b.tone}
            variant={b.tone === "on" ? "default" : "outline"}
            title={b.detail}
            className={cn(b.tone === "off" && "text-muted-foreground")}
          >
            {b.label}
          </Badge>
        ))}
      </div>
      <Progress value={completeness.score} label="Profile completeness" />
      {missing.length ? (
        <p className="text-xs text-muted-foreground">
          <span>Top missing: </span>
          {missing.map((m, i) => (
            <span key={m.key}>
              {onGoToStep ? (
                <button
                  type="button"
                  className="underline-offset-2 hover:underline"
                  onClick={() => onGoToStep(m.step)}
                >
                  {m.label}
                </button>
              ) : (
                m.label
              )}
              {i < missing.length - 1 ? ", " : ""}
            </span>
          ))}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">Nothing missing.</p>
      )}
    </div>
  );
}
