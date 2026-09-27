"use client";

import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import type { Profile } from "@/lib/api/browser";
import { getProfile, listItems, listProfiles } from "@/lib/onboarding/api";
import { fromApiRegion, STEPS, type Region, type StepId } from "@/lib/profile-fields";

import { ErrorBanner } from "./form";
import { Stepper } from "./stepper";
import { IdentityStep } from "./steps/identity-step";
import { PreferencesStep } from "./steps/preferences-step";
import { ProofStep } from "./steps/proof-step";
import { ReviewStep } from "./steps/review-step";
import { SellStep } from "./steps/sell-step";
import { SizeStep } from "./steps/size-step";
import { WhereStep } from "./steps/where-step";

export const REGION_STORAGE_KEY = "bidradar.onboarding.region";

function parseStep(value: string | null): StepId | null {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= STEPS.length ? (n as StepId) : null;
}

function parseRegion(value: string | null | undefined): Region | null {
  const v = value?.toUpperCase();
  return v === "US" || v === "IN" ? v : null;
}

export function OnboardingWizard() {
  const router = useRouter();
  const params = useSearchParams();

  const [profile, setProfile] = React.useState<Profile | null>(null);
  const [region, setRegion] = React.useState<Region | null>(null);
  const [step, setStep] = React.useState<StepId>(parseStep(params.get("step")) ?? 1);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<unknown>(null);
  const [ppCount, setPpCount] = React.useState<number | undefined>(undefined);

  const navigate = React.useCallback(
    (next: StepId) => {
      setStep(next);
      const search = new URLSearchParams(params.toString());
      search.set("step", String(next));
      router.replace(`/app/onboarding?${search.toString()}`, { scroll: true });
      window.scrollTo({ top: 0 });
    },
    [params, router],
  );

  // Resume: load the tenant's profile if one exists, else require a region choice.
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const profiles = await listProfiles();
        const wanted = params.get("profile");
        const existing = profiles.find((p) => p.id === wanted) ?? profiles.find((p) => p.is_active) ?? profiles[0];
        if (existing) {
          const [full, pp] = await Promise.all([
            getProfile(existing.id),
            listItems(existing.id, "past-performance").catch(() => []),
          ]);
          if (cancelled) return;
          setProfile(full);
          setRegion(fromApiRegion(full.region));
          setPpCount(pp.length);
          return;
        }
        let chosen = parseRegion(params.get("region"));
        if (!chosen) {
          try {
            chosen = parseRegion(window.sessionStorage.getItem(REGION_STORAGE_KEY));
          } catch {
            chosen = null;
          }
        }
        if (!chosen) {
          router.replace("/app/onboarding/region");
          return;
        }
        if (!cancelled) {
          setRegion(chosen);
          setStep(1);
        }
      } catch (e) {
        if (!cancelled) setError(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleComplete = React.useCallback(
    async (updated: Profile | null) => {
      const id = updated?.id ?? profile?.id;
      let latest = updated;
      if (id) {
        try {
          latest = await getProfile(id);
        } catch (e) {
          setError(e);
        }
      }
      if (latest) setProfile(latest);
      setError(null);
      if (step < STEPS.length) navigate((step + 1) as StepId);
    },
    [navigate, profile?.id, step],
  );

  const handleBack = step > 1 ? () => navigate((step - 1) as StepId) : undefined;

  if (loading) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading your profile…
      </p>
    );
  }
  if (!region) {
    return <ErrorBanner error={error ?? new Error("Choose a region to begin.")} />;
  }

  // Steps 2+ need a saved profile; send the user back to step 1 otherwise.
  const effectiveStep: StepId = profile ? step : 1;

  const stepProps = {
    profile,
    region,
    onBack: handleBack,
    onComplete: handleComplete,
    onProfileChange: setProfile,
    onPastPerformanceCount: setPpCount,
  };

  return (
    <div className="mx-auto grid w-full max-w-4xl gap-6">
      <Stepper
        current={effectiveStep}
        region={region}
        completeness={profile?.completeness ?? null}
        pastPerformanceCount={ppCount}
        canNavigate={Boolean(profile)}
        onNavigate={navigate}
      />
      <ErrorBanner error={error} />
      {effectiveStep === 1 ? <IdentityStep {...stepProps} /> : null}
      {effectiveStep === 2 && profile ? <SizeStep {...stepProps} profile={profile} /> : null}
      {effectiveStep === 3 && profile ? <SellStep {...stepProps} profile={profile} /> : null}
      {effectiveStep === 4 && profile ? <WhereStep {...stepProps} profile={profile} /> : null}
      {effectiveStep === 5 && profile ? <ProofStep {...stepProps} profile={profile} /> : null}
      {effectiveStep === 6 && profile ? <PreferencesStep {...stepProps} profile={profile} /> : null}
      {effectiveStep === 7 && profile ? (
        <ReviewStep {...stepProps} profile={profile} pastPerformanceCount={ppCount} onGoToStep={navigate} />
      ) : null}
    </div>
  );
}
