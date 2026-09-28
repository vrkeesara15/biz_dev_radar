import type { Metadata } from "next";

import { PursuitScreen } from "@/components/pursuits/pursuit-screen";

export const metadata: Metadata = { title: "Pursuit" };

export default async function PursuitPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PursuitScreen pursuitId={id} />;
}
