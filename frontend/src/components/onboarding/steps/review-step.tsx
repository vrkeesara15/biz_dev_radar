"use client";

import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { Profile } from "@/lib/api/browser";
import { describeMissing } from "@/lib/completeness";
import { formatMoney, type Currency } from "@/lib/money";
import { listItems, RESOURCES, type Resource } from "@/lib/onboarding/api";
import { CURRENCY_BY_REGION, STEPS, fieldLabel, type StepId } from "@/lib/profile-fields";

import { CompletenessMeter } from "../completeness-meter";
import { ErrorBanner, Section } from "../form";
import type { StepProps } from "../types";

const SECTION_LABELS: Record<string, { label: string; step: StepId }> = {
  identity: { label: "Identity", step: 1 },
  registrations: { label: "Registrations", step: 1 },
  size_finance: { label: "Size and finances", step: 2 },
  what_we_sell: { label: "What we sell", step: 3 },
  where_how_big: { label: "Where and how big", step: 4 },
  proof: { label: "Proof", step: 5 },
  preferences: { label: "Preferences", step: 6 },
};

const RESOURCE_LABELS: Record<Resource, string> = {
  codes: "Codes",
  keywords: "Keywords",
  "service-lines": "Service lines",
  certifications: "Certifications",
  "teaming-partners": "Teaming partners",
  "past-performance": "Past performance",
  personnel: "Key personnel",
  registrations: "Portal enrolments",
  vehicles: "Contract vehicles",
  insurance: "Insurance policies",
  boilerplate: "Boilerplate blocks",
  files: "Files",
  "rate-card": "Rate card entries",
};

export function ReviewStep({
  profile,
  region,
  onBack,
  pastPerformanceCount,
  onGoToStep,
}: StepProps & { profile: Profile; pastPerformanceCount?: number; onGoToStep: (step: StepId) => void }) {
  const [counts, setCounts] = React.useState<Partial<Record<Resource, number>>>({});
  const [error, setError] = React.useState<unknown>(null);
  const currency: Currency = CURRENCY_BY_REGION[region];

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const entries = await Promise.all(
          RESOURCES.map(async (r) => [r, (await listItems(profile.id, r)).length] as const),
        );
        if (!cancelled) setCounts(Object.fromEntries(entries));
      } catch (e) {
        if (!cancelled) setError(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [profile.id]);

  const completeness = profile.completeness;
  const sections = Object.entries(completeness.sections ?? {}) as [string, { score: number; weight: number; missing: string[] }][];
  const missing = describeMissing(completeness.missing);
  const valueMin = region === "US" ? profile.value_min_usd : profile.value_min_inr;
  const valueMax = region === "US" ? profile.value_max_usd : profile.value_max_inr;

  const facts: [string, React.ReactNode][] = [
    ["Legal name", profile.legal_name],
    ["Region", region === "IN" ? "India" : "United States"],
    ["Legal structure", profile.legal_structure ?? "—"],
    ["Addresses", String(profile.addresses?.length ?? 0)],
    [fieldLabel("employee_count_total"), profile.employee_count_total ?? "—"],
    [
      "Average turnover",
      profile.average_turnover ? formatMoney(profile.average_turnover.amount, profile.average_turnover.currency as Currency) : "—",
    ],
    ["Value range", valueMin || valueMax ? `${formatMoney(valueMin, currency, { fractionDigits: 0 })} – ${formatMoney(valueMax, currency, { fractionDigits: 0 })}` : "—"],
    ["Notice types", profile.notice_types_wanted?.length ? profile.notice_types_wanted.join(", ") : "—"],
    ["Output languages", profile.output_languages?.join(", ") || "—"],
  ];
  if (region === "US") {
    facts.splice(3, 0, ["UEI", profile.uei ?? "—"], ["SAM status", profile.sam_status ? `${profile.sam_status}${profile.sam_expires_on ? ` (expires ${profile.sam_expires_on})` : ""}` : "—"]);
  } else {
    facts.splice(3, 0, ["PAN", profile.pan ?? "—"], ["GSTIN", profile.gstin ?? "—"], ["Udyam", profile.udyam_number ? `${profile.udyam_number}${profile.udyam_category ? ` (${profile.udyam_category})` : ""}` : "—"]);
  }

  return (
    <div className="grid gap-6" aria-label="Review">
      <Section title="Completeness" description="Matching starts at 40. Drafting needs 70 and at least three past performance records.">
        <CompletenessMeter completeness={completeness} pastPerformanceCount={pastPerformanceCount} onGoToStep={onGoToStep} />
        <ul className="grid gap-2 sm:grid-cols-2">
          {sections.map(([key, s]) => {
            const meta = SECTION_LABELS[key] ?? { label: key, step: 7 as StepId };
            const pct = s.weight ? Math.round((s.score / s.weight) * 100) : 0;
            return (
              <li key={key} className="grid gap-1 rounded-lg border p-3">
                <div className="flex items-center justify-between gap-2 text-sm">
                  <button type="button" className="font-medium underline-offset-2 hover:underline" onClick={() => onGoToStep(meta.step)}>
                    {meta.label}
                  </button>
                  <span className="tabular-nums text-muted-foreground">
                    {s.score}/{s.weight}
                  </span>
                </div>
                <Progress value={pct} label={`${meta.label} completeness`} />
              </li>
            );
          })}
        </ul>
        {missing.length ? (
          <div className="grid gap-1">
            <h3 className="text-sm font-medium">Still missing</h3>
            <ul className="flex flex-wrap gap-1.5">
              {missing.map((m) => (
                <li key={m.key}>
                  <button type="button" onClick={() => onGoToStep(m.step)} className="rounded-full border px-2 py-0.5 text-xs hover:bg-muted">
                    {m.label} <span className="text-muted-foreground">· step {m.step}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Section>

      <Section title="Summary">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {facts.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 border-b py-1.5">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="text-right">{v}</dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-wrap gap-2">
          {RESOURCES.map((r) => (
            <Badge key={r} variant="outline" data-testid={`count-${r}`}>
              {RESOURCE_LABELS[r]}: {counts[r] ?? "…"}
            </Badge>
          ))}
        </div>
      </Section>

      <ErrorBanner error={error} />
      <div className="flex items-center justify-between gap-3 border-t pt-4">
        <Button type="button" variant="outline" onClick={onBack} disabled={!onBack}>
          Back
        </Button>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">Step 7 of {STEPS.length}</span>
          <Button render={<Link href="/app" />}>Go to dashboard</Button>
        </div>
      </div>
    </div>
  );
}
