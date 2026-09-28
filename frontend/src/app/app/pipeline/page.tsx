import type { Metadata } from "next";
import { Suspense } from "react";

import { PipelineScreen } from "@/components/pipeline/pipeline-screen";

export const metadata: Metadata = { title: "Pipeline" };

export default function PipelinePage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <PipelineScreen />
    </Suspense>
  );
}
