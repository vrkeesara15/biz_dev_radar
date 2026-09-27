import type { Profile } from "@/lib/api/browser";
import type { Region } from "@/lib/profile-fields";

export type StepProps = {
  /** Null only on step 1 before the profile has been created. */
  profile: Profile | null;
  region: Region;
  onBack?: () => void;
  /**
   * Called after the step's data is persisted. `profile` is the latest server
   * copy when the step touched the profile itself, else null; the wizard
   * refreshes completeness either way and advances.
   */
  onComplete: (profile: Profile | null) => Promise<void>;
  /** Mid-step profile updates (autofill, early creation). */
  onProfileChange: (profile: Profile) => void;
  onPastPerformanceCount?: (count: number) => void;
};
