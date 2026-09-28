/**
 * Pure draft logic for the workspace editor (M5-18): grounding flags to
 * editor decorations, the [NEEDS INPUT] chip reader, and the optimistic
 * versioning conflict.
 *
 * Nothing here touches the DOM, ProseMirror or the network, so every rule is
 * unit-testable; `components/pursuits/workspace/flag-highlight.ts` is the
 * thin ProseMirror plugin that turns `flagRanges` into decorations.
 */
import { ApiError } from "@/lib/opportunities/api";

// --- grounding flags -> ranges --------------------------------------------------

/** One entry of `draft.current.flags.flags` (backend core.grounding.Flag). */
export type GroundingFlag = { sentence: string; reason: string; detail?: string | null };

/** The whole `flags` object stored on a draft version. */
export type DraftFlags = {
  flags?: GroundingFlag[];
  supported_count?: number;
  unsupported_count?: number;
  placeholder_count?: number;
  sentences?: number;
  unresolved_tokens?: string[];
  red_team?: RedTeamBlock | null;
};

/** `flags.red_team` (backend core.red_team.flags_for_version). */
export type RedTeamIssue = {
  kind: string;
  requirement_id?: string | null;
  sentence?: string | null;
  fix_suggestion: string;
};
export type RedTeamBlock = {
  section_id?: string;
  revised?: boolean;
  issues?: RedTeamIssue[];
  page_estimate?: { pages?: number; limit?: number | null; over?: boolean } | null;
};

/** A half-open [from, to) span of the plain text a flag refers to. */
export type FlagRange = {
  from: number;
  to: number;
  sentence: string;
  reason: string;
  detail: string;
};

const ESCAPE_RE = /[.*+?^${}()|[\]\\]/g;

/**
 * A regex that matches `sentence` allowing any run of whitespace where the
 * sentence has one: the flag was computed over `body_text` (HTML flattened to
 * text) and the editor holds the same words with different wrapping.
 */
function loosely(sentence: string): RegExp | null {
  const trimmed = sentence.trim();
  if (!trimmed) return null;
  const pattern = trimmed
    .split(/\s+/)
    .map((word) => word.replace(ESCAPE_RE, "\\$&"))
    .join("\\s+");
  return new RegExp(pattern, "g");
}

/**
 * Where each flagged sentence sits inside `text`.
 *
 * Only flags that are actually found are returned (a sentence the writer has
 * since deleted must not paint an unrelated paragraph red), the result is
 * sorted by position, and overlapping matches are dropped so a decoration set
 * never double-marks the same words. Matching is case-sensitive first and
 * case-insensitive second, because the model copies the sentence verbatim but
 * a writer may have changed its capitalisation.
 */
export function flagRanges(text: string, flags: readonly GroundingFlag[] | null | undefined): FlagRange[] {
  if (!text || !flags?.length) return [];
  const found: FlagRange[] = [];
  const used: [number, number][] = [];
  const overlaps = (from: number, to: number) =>
    used.some(([a, b]) => from < b && a < to);

  for (const flag of flags) {
    const sentence = (flag?.sentence ?? "").trim();
    if (!sentence) continue;
    const exact = loosely(sentence);
    if (!exact) continue;
    let match: RegExpExecArray | null = null;
    for (const regex of [exact, new RegExp(exact.source, "gi")]) {
      regex.lastIndex = 0;
      let candidate: RegExpExecArray | null;
      while ((candidate = regex.exec(text)) !== null) {
        if (candidate[0].length === 0) break;
        if (!overlaps(candidate.index, candidate.index + candidate[0].length)) {
          match = candidate;
          break;
        }
      }
      if (match) break;
    }
    if (!match) continue;
    const from = match.index;
    const to = from + match[0].length;
    used.push([from, to]);
    found.push({
      from,
      to,
      sentence,
      reason: flag.reason ?? "unsupported",
      detail: flag.detail ?? "",
    });
  }
  return found.sort((a, b) => a.from - b.from);
}

/** The tooltip an unsupported-claim decoration carries. */
export function flagTitle(range: Pick<FlagRange, "reason" | "detail">): string {
  const reason = range.reason.replace(/_/g, " ");
  return range.detail ? `Unsupported claim (${reason}): ${range.detail}` : `Unsupported claim (${reason})`;
}

// --- [NEEDS INPUT: ...] chips ----------------------------------------------------

/** One entry of `draft.current.needs_input` (agents/drafters.py). */
export type NeedsInputItem = {
  placeholder: string;
  question?: string | null;
  task_id?: string | null;
};

export type NeedsInputChip = {
  from: number;
  to: number;
  /** The text inside the marker, e.g. "labor category"; "" for a bare marker. */
  label: string;
  /** The whole marker as it appears in the text. */
  marker: string;
  question: string | null;
  taskId: string | null;
};

// Mirrors backend core.collab.PLACEHOLDER_RE / BARE_PLACEHOLDER_RE: never
// spans a line, so a stray bracket cannot swallow a paragraph.
export const NEEDS_INPUT_RE = /\[\s*NEEDS\s+INPUT\s*(?::\s*([^\]\n]{0,200}?)\s*)?\]/gi;

const fold = (value: string) => value.trim().toLowerCase().replace(/\s+/g, " ");

/**
 * Every `[NEEDS INPUT: ...]` marker in `text`, matched back to the stored
 * needs_input item that carries its question and the task the drafter opened
 * for it (M6-07 / OQ-119), so the chip can link to that task.
 */
export function needsInputChips(
  text: string,
  items: readonly NeedsInputItem[] | null | undefined,
): NeedsInputChip[] {
  if (!text) return [];
  const byMarker = new Map<string, NeedsInputItem>();
  const byLabel = new Map<string, NeedsInputItem>();
  for (const item of items ?? []) {
    const marker = fold(item.placeholder ?? "");
    if (marker) byMarker.set(marker, item);
    const inner = NEEDS_INPUT_RE.exec(item.placeholder ?? "");
    NEEDS_INPUT_RE.lastIndex = 0;
    const label = fold(inner?.[1] ?? "");
    if (label && !byLabel.has(label)) byLabel.set(label, item);
  }

  const chips: NeedsInputChip[] = [];
  NEEDS_INPUT_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = NEEDS_INPUT_RE.exec(text)) !== null) {
    const marker = match[0];
    const label = (match[1] ?? "").trim();
    const item = byMarker.get(fold(marker)) ?? byLabel.get(fold(label)) ?? null;
    chips.push({
      from: match.index,
      to: match.index + marker.length,
      label,
      marker,
      question: item?.question?.trim() || null,
      taskId: item?.task_id ?? null,
    });
  }
  return chips;
}

/** The needs_input items no marker in the body accounts for (answered or moved). */
export function orphanNeedsInput(
  text: string,
  items: readonly NeedsInputItem[] | null | undefined,
): NeedsInputItem[] {
  const seen = new Set(needsInputChips(text, items).map((chip) => fold(chip.marker)));
  return (items ?? []).filter((item) => !seen.has(fold(item.placeholder ?? "")));
}

// --- optimistic versioning -------------------------------------------------------

/**
 * A refused save: `PUT /pursuits/{id}/drafts/{section}` answers 409 with a
 * sentence, not a structured body —
 *   "draft past-performance is at version 4, not 3; reload the section and
 *    reapply your edit"
 * so the current version is read back out of it when it is there, and the
 * conflict still stands (with the server's own sentence) when it is not.
 */
export type StaleVersion = {
  error: "stale_version";
  /** The version the editor sent, when the message names it. */
  baseVersion: number | null;
  /** The version the server holds, when the message names it. */
  currentVersion: number | null;
  message: string;
};

const STALE_RE = /at version\s+(\d+),\s*not\s+(\d+)/i;

const detailText = (body: unknown): string => {
  if (typeof body === "string") return body;
  if (!body || typeof body !== "object") return "";
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message ?? "");
  }
  return "";
};

export const STALE_FALLBACK =
  "Someone else saved this section while you were editing. Reload it and reapply your edit.";

/** The conflict behind a failed save, or null when the failure is something else. */
export function asStaleVersion(error: unknown): StaleVersion | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const message = detailText(error.body).trim();
  const match = STALE_RE.exec(message);
  return {
    error: "stale_version",
    currentVersion: match ? Number(match[1]) : null,
    baseVersion: match ? Number(match[2]) : null,
    message: message || STALE_FALLBACK,
  };
}

/** The version the editor should reload to, given a conflict and what it holds. */
export function reloadVersion(conflict: StaleVersion, loaded: number): number {
  return conflict.currentVersion ?? loaded;
}
