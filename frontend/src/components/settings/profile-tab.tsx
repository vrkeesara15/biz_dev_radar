"use client";

import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ApiError, listProfiles } from "@/lib/onboarding/api";
import type { Profile } from "@/lib/api/browser";
import { STEPS, fromApiRegion } from "@/lib/profile-fields";
import { topMissing } from "@/lib/completeness";

/**
 * Settings > Profile. The company profile is edited in the onboarding wizard
 * (SPEC 4 / 10.4 screen 1), so this tab is the way back into each of its seven
 * steps, with the completeness meter as it stands.
 */
export function ProfileTab() {
  const [profiles, setProfiles] = React.useState<Profile[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    listProfiles()
      .then((rows) => {
        if (!cancelled) setProfiles(rows);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(caught instanceof ApiError ? caught.message : "Profiles could not be read.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (!profiles) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="grid gap-4" data-testid="profile-tab">
      {!profiles.length ? (
        <Card>
          <CardHeader>
            <CardTitle>No company profile yet</CardTitle>
            <CardDescription>
              Matching and drafting both run per profile, so this is the first thing to fill in.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button render={<Link href="/app/onboarding/region" />}>Start the wizard</Button>
          </CardContent>
        </Card>
      ) : null}

      {profiles.map((profile) => {
        const completeness = profile.completeness;
        const score = completeness?.score ?? 0;
        const missing = topMissing(completeness?.missing ?? [], 3);
        return (
          <Card key={profile.id} data-testid="profile-card">
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span>{profile.legal_name || "Untitled profile"}</span>
                <Badge variant="outline">{fromApiRegion(profile.region)}</Badge>
              </CardTitle>
              <CardDescription>
                {completeness?.matching_enabled ? "Matching on" : "Matching off"} ·{" "}
                {completeness?.drafting_enabled ? "Drafting on" : "Drafting off"}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              <div className="grid gap-1.5">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">Completeness</span>
                  <span className="tabular-nums">{score}%</span>
                </div>
                <Progress value={score} label={`Profile completeness ${score} percent`} />
                {missing.length ? (
                  <p className="text-xs text-muted-foreground">
                    Next: {missing.map((item) => item.label).join(", ")}
                  </p>
                ) : null}
              </div>

              <ul className="grid gap-1.5 sm:grid-cols-2">
                {STEPS.map((step) => (
                  <li key={step.id}>
                    <Link
                      href={`/app/onboarding?step=${step.id}`}
                      className="flex items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
                    >
                      <span>
                        <span className="text-muted-foreground tabular-nums">{step.id}. </span>
                        {step.title}
                      </span>
                      <span className="text-xs text-muted-foreground">Edit →</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
