import type { Metadata } from "next";
import { Suspense } from "react";

import { CalendarScreen } from "@/components/calendar/calendar-screen";

export const metadata: Metadata = { title: "Calendar" };

export default function CalendarPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <CalendarScreen />
    </Suspense>
  );
}
