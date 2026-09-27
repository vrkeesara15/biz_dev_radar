import { CheckIcon, CircleHelpIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { eligibilityChecks, type CheckStatus } from "@/lib/opportunities/eligibility";
import { cn } from "@/lib/utils";

const ICON: Record<CheckStatus, { Icon: typeof CheckIcon; className: string; label: string }> = {
  pass: { Icon: CheckIcon, className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-200", label: "Pass" },
  fail: { Icon: XIcon, className: "bg-destructive/10 text-destructive", label: "Fail" },
  unknown: { Icon: CircleHelpIcon, className: "bg-muted text-muted-foreground", label: "Unknown" },
};

export function StatusIcon({ status, className }: { status: CheckStatus; className?: string }) {
  const { Icon, className: tone, label } = ICON[status];
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn("inline-flex size-5 shrink-0 items-center justify-center rounded-full", tone, className)}
    >
      <Icon className="size-3" aria-hidden="true" />
    </span>
  );
}

export function EligibilityList({ eligibility, className }: { eligibility: unknown; className?: string }) {
  const summary = eligibilityChecks(eligibility);
  return (
    <Card data-testid="eligibility-card" className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Eligibility checks
          {summary.overall ? <StatusIcon status={summary.overall} /> : null}
        </CardTitle>
        <CardDescription>
          {summary.overall
            ? "Evaluated against your active profile."
            : "As extracted from the notice; pass/fail appears once matched to your profile."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {summary.checks.length === 0 ? (
          <p className="text-sm text-muted-foreground">No eligibility conditions extracted yet. Check the official documents.</p>
        ) : (
          <ul className="divide-y">
            {summary.checks.map((check) => (
              <li key={check.key} className="flex items-start gap-3 py-2" data-status={check.status}>
                <StatusIcon status={check.status} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{check.label}</p>
                  {check.detail ? <p className="text-sm text-muted-foreground">{check.detail}</p> : null}
                </div>
                {check.citation ? (
                  <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground">
                    {check.citation}
                  </Badge>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {summary.blocking.length ? (
          <p className="text-xs text-destructive">Blocks submission: {summary.blocking.join(", ")}</p>
        ) : null}
        {summary.exemptions.length ? (
          <p className="text-xs text-muted-foreground">Exemptions applied: {summary.exemptions.join(", ")}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
