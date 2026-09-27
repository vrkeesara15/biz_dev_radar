import type { Metadata } from "next";

import { RegionChoice } from "@/components/onboarding/region-choice";

export const metadata: Metadata = { title: "Choose region" };

export default function RegionPage() {
  return <RegionChoice />;
}
