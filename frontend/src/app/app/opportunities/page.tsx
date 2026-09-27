import type { Metadata } from "next";
import { Suspense } from "react";

import { SearchScreen } from "@/components/opportunities/search-screen";

export const metadata: Metadata = { title: "Opportunities" };

export default function OpportunitiesPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <SearchScreen />
    </Suspense>
  );
}
