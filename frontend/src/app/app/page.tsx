import type { Metadata } from "next";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata: Metadata = { title: "Home" };

const TILES = [
  { title: "High-fit today", description: "New matches scoring High-fit" },
  { title: "Due this week", description: "Pursuits with deadlines in 7 days" },
  { title: "Pipeline value", description: "Estimated value of active pursuits" },
] as const;

export default function HomePage() {
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Home</h1>
        <p className="text-sm text-muted-foreground">
          Today&apos;s matches, deadlines and pipeline at a glance.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {TILES.map((tile) => (
          <Card key={tile.title}>
            <CardHeader>
              <CardTitle>{tile.title}</CardTitle>
              <CardDescription>{tile.description}</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-semibold tabular-nums text-muted-foreground">
                —
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
