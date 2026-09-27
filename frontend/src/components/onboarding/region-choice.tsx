"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import type { Region } from "@/lib/profile-fields";

import { REGION_STORAGE_KEY } from "./wizard";

const REGIONS: { value: Region; title: string; description: string; examples: string }[] = [
  {
    value: "US",
    title: "United States",
    description: "SAM.gov, Grants.gov and USAspending. UEI, CAGE, NAICS/PSC codes and SBA size standards.",
    examples: "Data stays in the US region.",
  },
  {
    value: "IN",
    title: "India",
    description: "GeM, CPPP and state portals. PAN, GSTIN, Udyam, DPIIT and GeM categories.",
    examples: "Data stays in the India region (Mumbai).",
  },
];

export function RegionChoice() {
  const router = useRouter();
  const [selected, setSelected] = React.useState<Region | null>(null);

  const proceed = () => {
    if (!selected) return;
    try {
      window.sessionStorage.setItem(REGION_STORAGE_KEY, selected);
    } catch {
      // storage unavailable; the query parameter carries the choice
    }
    router.push(`/app/onboarding?region=${selected}&step=1`);
  };

  return (
    <div className="mx-auto grid w-full max-w-2xl gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Where do you bid?</h1>
        <p className="text-sm text-muted-foreground">
          Your region decides which sources we watch and which registrations the profile asks for. It is stored
          on the profile and cannot be changed later without creating a new profile.
        </p>
      </div>
      <fieldset className="grid gap-3 sm:grid-cols-2">
        <legend className="sr-only">Region</legend>
        {REGIONS.map((r) => {
          const id = `region-${r.value}`;
          const active = selected === r.value;
          return (
            <label
              key={r.value}
              htmlFor={id}
              className={
                "grid cursor-pointer gap-1 rounded-xl border p-4 transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50 " +
                (active ? "border-foreground bg-muted" : "hover:bg-muted/50")
              }
            >
              <span className="flex items-center gap-2">
                <input
                  id={id}
                  type="radio"
                  name="region"
                  value={r.value}
                  checked={active}
                  onChange={() => setSelected(r.value)}
                  className="accent-primary"
                />
                <span className="text-sm font-semibold">{r.title}</span>
              </span>
              <span className="text-sm text-muted-foreground">{r.description}</span>
              <span className="text-xs text-muted-foreground">{r.examples}</span>
            </label>
          );
        })}
      </fieldset>
      <div>
        <Button type="button" onClick={proceed} disabled={!selected}>
          Continue
        </Button>
      </div>
    </div>
  );
}
