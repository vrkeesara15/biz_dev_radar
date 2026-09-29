"use client";

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import { relativeTime } from "@/lib/notifications/api";
import { ApiError } from "@/lib/opportunities/api";
import type { PursuitOut } from "@/lib/pursuits/api";
import { recordDecision } from "@/lib/pursuits/workspace-api";
import { SCORECARD_CRITERIA, type Scorecard } from "@/lib/pursuits/workspace";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

/** The six criteria, the gaps, the teaming suggestions and the recommendation. */
export function ScorecardPanel({ scorecard }: { scorecard: Scorecard }) {
  return (
    <Card data-testid="scorecard">
      <CardHeader>
        <CardTitle className="text-sm">Bid/no-bid scorecard</CardTitle>
        <CardDescription>
          Agent 4 scored this notice against the profile.
          {scorecard.version ? ` Version ${scorecard.version}.` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid gap-3 sm:grid-cols-2">
          {SCORECARD_CRITERIA.map((criterion) => {
            const value = scorecard[criterion.key] as number;
            return (
              // M7-13: a <dl> may hold <div> groups, but each group must contain
              // only <dt>/<dd> — a wrapper div or a bare progressbar inside one
              // breaks the list for a screen reader (axe `definition-list`). The
              // bar is therefore a second <dd> of the same term.
              <div
                key={criterion.key}
                data-testid="scorecard-criterion"
                data-criterion={criterion.key}
                className="grid grid-cols-[1fr_auto] items-baseline gap-x-2 text-sm"
              >
                <dt>{criterion.label}</dt>
                <dd className="tabular-nums font-medium">{value}</dd>
                <dd className="col-span-2">
                  <Progress value={value} label={`${criterion.label} score`} className="mt-1" />
                </dd>
              </div>
            );
          })}
        </dl>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge data-testid="scorecard-recommendation" className="capitalize">
            Recommends {scorecard.recommendation.replace(/_/g, "-")}
          </Badge>
          {scorecard.weighted_score ? (
            <span className="text-muted-foreground">Weighted score {scorecard.weighted_score}</span>
          ) : null}
          {scorecard.suggested_recommendation &&
          scorecard.suggested_recommendation !== scorecard.recommendation ? (
            <Badge variant="outline">
              Weights suggest {scorecard.suggested_recommendation.replace(/_/g, "-")}
            </Badge>
          ) : null}
        </div>

        {scorecard.reasons.length ? (
          <div>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">Reasons</h3>
            <ul className="mt-1 grid list-disc gap-1 pl-5 text-sm">
              {scorecard.reasons.map((reason, index) => (
                <li key={index} data-testid="scorecard-reason">
                  {reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {scorecard.incumbent_note ? (
          <p className="text-sm text-muted-foreground">{scorecard.incumbent_note}</p>
        ) : null}

        {scorecard.gaps.length ? (
          <div>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">Gaps</h3>
            <ul className="mt-1 grid gap-1.5 text-sm">
              {scorecard.gaps.map((gap, index) => (
                <li key={index} data-testid="scorecard-gap" className="rounded-lg border p-2">
                  <p className="font-medium">{gap.gap}</p>
                  <p className="text-muted-foreground">{gap.suggested_fix}</p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {scorecard.teaming_suggestions.length ? (
          <div>
            <h3 className="text-xs uppercase tracking-wide text-muted-foreground">Teaming</h3>
            <ul className="mt-1 grid gap-1.5 text-sm">
              {scorecard.teaming_suggestions.map((row, index) => (
                <li key={index} data-testid="scorecard-teaming" className="rounded-lg border p-2">
                  <p className="font-medium">{row.partner_or_capability}</p>
                  <p className="text-muted-foreground">{row.why}</p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function BidNoBidTab({
  pursuit,
  scorecard,
  mayDecide,
  ownerName,
  onDecided,
}: {
  pursuit: PursuitOut;
  scorecard: Scorecard | null;
  mayDecide: boolean;
  /** Resolves the decider's id to a name, when the member list is loaded. */
  ownerName: (userId: string | null) => string;
  onDecided: (pursuit: PursuitOut) => void;
}) {
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState<string | null>(null);
  const decided = !!pursuit.decision;

  const decide = async (decision: "bid" | "no_bid") => {
    setBusy(decision);
    try {
      const result = await recordDecision(pursuit.id, decision, note);
      onDecided(result.pursuit);
      toast.success(decision === "bid" ? "Recorded: bid" : "Recorded: no-bid");
    } catch (caught) {
      toast.error(describe(caught, "That decision was refused"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="bid-no-bid-tab">
      {scorecard ? (
        <ScorecardPanel scorecard={scorecard} />
      ) : (
        <Card data-testid="scorecard-missing">
          <CardHeader>
            <CardTitle className="text-sm">No scorecard to show</CardTitle>
            <CardDescription>
              Agent 4 stores its scorecard — fit, eligibility, capacity, competition, value, win
              probability, gaps, teaming and reasons — as a pursuit artifact, but no API route serves
              stored artifacts yet, so it can only appear here once one does. Run the bid/no-bid agent
              from the header to produce it.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <Card data-testid="gate-1">
        <CardHeader>
          <CardTitle className="text-sm">Gate 1 — bid or no-bid</CardTitle>
          <CardDescription>
            A human decides; the pipeline waits here. A bid moves the pursuit to Drafting and resumes
            the run, a no-bid closes it.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          {decided ? (
            <p data-testid="decision-record" className="rounded-lg border bg-muted/40 p-3 text-sm">
              <strong className="capitalize">{pursuit.decision?.replace(/_/g, "-")}</strong> by{" "}
              {ownerName(pursuit.decided_by)}
              {pursuit.decided_at ? ` · ${new Date(pursuit.decided_at).toLocaleString()}` : ""}
              {pursuit.decided_at ? (
                <span className="text-muted-foreground"> ({relativeTime(pursuit.decided_at)})</span>
              ) : null}
              {pursuit.decision_note ? (
                <span className="mt-1 block text-muted-foreground">“{pursuit.decision_note}”</span>
              ) : null}
            </p>
          ) : null}

          {mayDecide ? (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="decision-note">Note</Label>
                <Textarea
                  id="decision-note"
                  rows={2}
                  value={note}
                  disabled={decided}
                  placeholder="Why this call? Recorded with the decision."
                  onChange={(event) => setNote(event.target.value)}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  data-testid="decide-bid"
                  disabled={decided || busy !== null}
                  onClick={() => void decide("bid")}
                >
                  Bid
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  data-testid="decide-no-bid"
                  disabled={decided || busy !== null}
                  onClick={() => void decide("no_bid")}
                >
                  No-bid
                </Button>
              </div>
              {decided ? (
                <p className="text-xs text-muted-foreground">
                  The decision is recorded and cannot be changed here.
                </p>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="gate-1-readonly">
              Your role cannot record this decision. SPEC 3 gives bid/no-bid approval to the bid
              manager and the tenant owner (plus any role the profile names as a required approver).
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
