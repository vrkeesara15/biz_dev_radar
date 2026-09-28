/**
 * Key-date kinds on the client (SPEC 9), mirroring `app.core.key_dates`.
 *
 * The auto kinds are created from the notice and at most one of each exists
 * per pursuit (the database enforces it), so the "Add a date" dialog offers
 * only the kinds the pursuit does not already carry, plus `custom`, which may
 * repeat. The server owns the labels too; ours are the fallback for a row the
 * user never renamed and for the dialog's own menu.
 */
export const AUTO_KEY_DATE_KINDS = [
  "questions_due",
  "prebid_meeting",
  "internal_draft",
  "internal_review",
  "internal_final",
  "portal_submission",
  "emd_bg_ready",
  "dsc_check",
] as const;

export const KEY_DATE_CUSTOM = "custom";
export const KEY_DATE_KINDS = [...AUTO_KEY_DATE_KINDS, KEY_DATE_CUSTOM] as const;
export type KeyDateKind = (typeof KEY_DATE_KINDS)[number];

export const KEY_DATE_LABELS: Record<KeyDateKind, string> = {
  questions_due: "Questions due",
  prebid_meeting: "Pre-bid meeting",
  internal_draft: "Internal draft complete",
  internal_review: "Internal review",
  internal_final: "Internal final",
  portal_submission: "Portal submission due",
  emd_bg_ready: "EMD / bank guarantee ready",
  dsc_check: "DSC check",
  custom: "Key date",
};

/** India-only kinds (SPEC 9); hidden from a US pursuit's "Add a date" menu. */
export const IN_ONLY_KINDS: readonly string[] = ["emd_bg_ready", "dsc_check"];

export function isKeyDateKind(value: unknown): value is KeyDateKind {
  return typeof value === "string" && (KEY_DATE_KINDS as readonly string[]).includes(value);
}

export function keyDateKindLabel(kind: string): string {
  if (isKeyDateKind(kind)) return KEY_DATE_LABELS[kind];
  const spaced = kind.replace(/_/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : KEY_DATE_LABELS.custom;
}

/**
 * Kinds the "Add a date" menu may offer: `custom` always, plus every auto kind
 * the pursuit does not already have (an India-only kind only for an IN pursuit).
 */
export function addableKinds(
  existing: readonly { kind: string }[],
  region: string | null | undefined,
): KeyDateKind[] {
  const taken = new Set(existing.map((row) => row.kind));
  const auto = AUTO_KEY_DATE_KINDS.filter((kind) => {
    if (taken.has(kind)) return false;
    if (IN_ONLY_KINDS.includes(kind) && region !== "in") return false;
    return true;
  });
  return [KEY_DATE_CUSTOM, ...auto];
}

/** "2026-10-14T18:00" (a datetime-local value) -> "2026-10-14T18:00:00.000Z"-ish ISO. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

/** An ISO instant -> the `datetime-local` input value in the viewer's zone. */
export function isoToLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}
