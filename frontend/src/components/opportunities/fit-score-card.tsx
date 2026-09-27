import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Match, MatchSignal } from "@/lib/opportunities/api";
import { cn } from "@/lib/utils";

/** SPEC 6 stage-2 signals with default weights; the layout the real breakdown fills in. */
export const DEFAULT_SIGNALS: { key: string; label: string; weight: number }[] = [
  { key: "code_match", label: "Code match", weight: 25 },
  { key: "semantic_similarity", label: "Semantic similarity", weight: 25 },
  { key: "eligibility", label: "Eligibility", weight: 15 },
  { key: "keyword_match", label: "Keyword match", weight: 10 },
  { key: "past_performance", label: "Past-performance relevance", weight: 10 },
  { key: "value_fit", label: "Value fit", weight: 5 },
  { key: "geography", label: "Geography", weight: 5 },
  { key: "buyer_affinity", label: "Buyer affinity", weight: 5 },
];

const BAND_LABEL = { high: "High fit", medium: "Medium fit", low: "Low fit" } as const;
const BAND_CLASS = {
  high: "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  medium: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
  low: "border-transparent bg-muted text-muted-foreground",
} as const;

function SignalRow({ signal, placeholder }: { signal: MatchSignal; placeholder?: boolean }) {
  const weight = signal.weight ?? 0;
  const points = signal.points ?? (signal.value !== null && signal.weight !== null ? signal.value * signal.weight : null);
  const ratio = points !== null && weight > 0 ? Math.min(1, Math.max(0, points / weight)) : 0;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-1.5">
      <span className={cn("truncate text-sm", placeholder && "text-muted-foreground")}>{signal.label}</span>
      <span className="text-right text-xs tabular-nums text-muted-foreground">
        {points !== null ? `${Math.round(points * 10) / 10} / ${weight}` : `— / ${weight || "—"}`}
      </span>
      <div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
        <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${ratio * 100}%` }} />
      </div>
      {signal.note ? <p className="col-span-2 text-xs text-muted-foreground">{signal.note}</p> : null}
    </li>
  );
}

export function FitScoreCard({ match, className }: { match: Match | null; className?: string }) {
  const scored = match !== null && match.score !== null;
  const signals: MatchSignal[] =
    match && match.breakdown.length
      ? match.breakdown
      : DEFAULT_SIGNALS.map((s) => ({ key: s.key, label: s.label, weight: s.weight, value: null, points: null, note: null }));

  return (
    <Card data-testid="fit-score-card" className={className}>
      <CardHeader>
        <CardTitle>Fit score</CardTitle>
        <CardDescription>
          {scored
            ? "Rules, embeddings and an AI rationale against your active profile."
            : "Scored against your active profile once matching runs for it."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex items-baseline gap-3">
          {scored ? (
            <>
              <span className="text-4xl font-semibold tabular-nums" aria-label={`Fit score ${Math.round(match.score!)} out of 100`}>
                {Math.round(match.score!)}
              </span>
              <span className="text-sm text-muted-foreground">/ 100</span>
              {match.band ? (
                <Badge variant="outline" className={BAND_CLASS[match.band]}>
                  {match.label ?? BAND_LABEL[match.band]}
                </Badge>
              ) : null}
            </>
          ) : (
            <>
              <span className="text-4xl font-semibold text-muted-foreground" aria-hidden="true">
                —
              </span>
              <span className="text-sm font-medium" data-testid="not-scored">
                Not scored yet
              </span>
            </>
          )}
        </div>

        <section aria-labelledby="fit-breakdown-heading">
          <h3 id="fit-breakdown-heading" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Breakdown
          </h3>
          <ul className="mt-1 divide-y">
            {signals.map((signal) => (
              <SignalRow key={signal.key} signal={signal} placeholder={!scored} />
            ))}
          </ul>
        </section>

        <section aria-labelledby="fit-rationale-heading" className="grid gap-3">
          <h3 id="fit-rationale-heading" className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Rationale
          </h3>
          {scored && (match.fitSummary.length || match.gaps.length || match.risks.length) ? (
            <>
              {match.fitSummary.length ? (
                <div>
                  <h4 className="text-sm font-medium">Why it fits</h4>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                    {match.fitSummary.slice(0, 3).map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {match.matchedCapabilities.length ? (
                <p className="text-sm">
                  <span className="font-medium">Matched capabilities: </span>
                  {match.matchedCapabilities.join(", ")}
                </p>
              ) : null}
              {match.gaps.length ? (
                <div>
                  <h4 className="text-sm font-medium">Gaps</h4>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                    {match.gaps.map((gap, index) => (
                      <li key={index}>
                        {gap.text}
                        {gap.fix ? <span className="text-muted-foreground"> — suggested fix: {gap.fix}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {match.risks.length ? (
                <div>
                  <h4 className="text-sm font-medium">Eligibility risks</h4>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                    {match.risks.map((risk, index) => (
                      <li key={index}>
                        {risk.text}
                        {risk.citation ? <span className="text-muted-foreground"> ({risk.citation})</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {match.recommendedAction || match.confidence !== null ? (
                <p className="text-xs text-muted-foreground">
                  {match.recommendedAction ? `Recommended: ${match.recommendedAction}` : null}
                  {match.recommendedAction && match.confidence !== null ? " · " : null}
                  {match.confidence !== null ? `Confidence ${Math.round(match.confidence * 100)}%` : null}
                </p>
              ) : null}
            </>
          ) : (
            <dl className="grid gap-2 text-sm text-muted-foreground">
              <div>
                <dt className="font-medium text-foreground/80">Why it fits</dt>
                <dd>Three bullets appear here for scores of 50 and above.</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground/80">Gaps</dt>
                <dd>Each with a suggested fix: teaming partner, hire or certification.</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground/80">Eligibility risks</dt>
                <dd>With page citations into the solicitation documents.</dd>
              </div>
            </dl>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
