import type { Metadata } from "next";

import { DetailScreen } from "@/components/opportunities/detail-screen";

export const metadata: Metadata = { title: "Opportunity" };

export default async function OpportunityDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DetailScreen id={id} />;
}
