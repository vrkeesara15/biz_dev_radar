import type { Metadata } from "next";
import { Suspense } from "react";

import { OnboardingWizard } from "@/components/onboarding/wizard";

export const metadata: Metadata = { title: "Onboarding" };

export default function OnboardingPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <OnboardingWizard />
    </Suspense>
  );
}
