import type { Metadata } from "next";
import Link from "next/link";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { searchHref } from "@/lib/opportunities/filters";

export const metadata: Metadata = { title: "Home" };

const TILES = [
  {
    title: "High-fit today",
    description: "New matches scoring High-fit",
    href: searchHref({ min_score: 70 }),
    cta: "Open search",
  },
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
              <CardTitle>
                {"href" in tile ? (
                  <Link href={tile.href} className="underline-offset-4 hover:underline">
                    {tile.title}
                  </Link>
                ) : (
                  tile.title
                )}
              </CardTitle>
              <CardDescription>{tile.description}</CardDescription>
            </CardHeader>
            <CardContent className="flex items-end justify-between gap-3">
              <p className="text-2xl font-semibold tabular-nums text-muted-foreground">
                —
              </p>
              {"href" in tile ? (
                <Link href={tile.href} className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
                  {tile.cta} →
                </Link>
              ) : null}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
