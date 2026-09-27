import type { Metadata } from "next";

import { SavedSearchesScreen } from "@/components/settings/saved-searches-screen";

export const metadata: Metadata = { title: "Saved searches" };

export default function SavedSearchesPage() {
  return <SavedSearchesScreen />;
}
