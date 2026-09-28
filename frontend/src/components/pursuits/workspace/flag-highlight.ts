"use client";

import { Extension } from "@tiptap/react";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { EditorState } from "@tiptap/pm/state";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";

import {
  flagRanges,
  flagTitle,
  needsInputChips,
  type GroundingFlag,
  type NeedsInputItem,
} from "@/lib/pursuits/drafts";

export type HighlightInput = {
  flags: GroundingFlag[];
  needsInput: NeedsInputItem[];
};

/**
 * The extension reads its input through a getter rather than holding it,
 * because a TipTap extension's `options` are read-only once configured and a
 * new draft version must repaint without remounting the editor.
 */
export type HighlightOptions = { source: () => HighlightInput };

export const flagHighlightKey = new PluginKey<DecorationSet>("bidradar-grounding-flags");

/**
 * Inline decorations built from the version's grounding report: every
 * sentence the validator could not ground is painted red (SPEC 8: "unsupported
 * claims are highlighted red"), and every `[NEEDS INPUT: ...]` marker is
 * painted amber.
 *
 * Offsets come from `lib/pursuits/drafts`, which works on plain text, so the
 * walk is per text block and the mapping is `blockPos + 1 + offset`. That is
 * exact for a paragraph of text and text marks (links, bold), which is what a
 * draft section is; an inline node whose size differs from its text length
 * would shift the tail of that one block, so a decoration is dropped rather
 * than drawn when it would fall outside the block.
 */
function decorations(doc: ProseMirrorNode, options: HighlightInput): DecorationSet {
  const found: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true;
    const text = node.textContent;
    if (!text) return false;
    const base = pos + 1;
    const end = pos + node.nodeSize - 1;
    const add = (from: number, to: number, attrs: Record<string, string>) => {
      const start = base + from;
      const stop = base + to;
      if (stop > end) return;
      found.push(Decoration.inline(start, stop, attrs));
    };
    for (const range of flagRanges(text, options.flags)) {
      add(range.from, range.to, {
        class: "bidradar-unsupported",
        title: flagTitle(range),
        "data-unsupported": "true",
      });
    }
    for (const chip of needsInputChips(text, options.needsInput)) {
      add(chip.from, chip.to, {
        class: "bidradar-needs-input",
        title: chip.question ?? "An agent refused to invent this; a task is open for it.",
        "data-needs-input": chip.label || "unspecified input",
      });
    }
    return false;
  });
  return DecorationSet.create(doc, found);
}

/** The extension; `options.source()` is re-read on every repaint. */
export const FlagHighlight = Extension.create<HighlightOptions>({
  name: "bidradarFlagHighlight",

  addOptions() {
    return { source: () => ({ flags: [], needsInput: [] }) };
  },

  addProseMirrorPlugins() {
    const source = this.options.source;
    return [
      new Plugin<DecorationSet>({
        key: flagHighlightKey,
        state: {
          init: (_config, state: EditorState) => decorations(state.doc, source()),
          apply: (tr, value) =>
            tr.docChanged || tr.getMeta(flagHighlightKey)
              ? decorations(tr.doc, source())
              : value,
        },
        props: {
          decorations(state) {
            return flagHighlightKey.getState(state) ?? DecorationSet.empty;
          },
        },
      }),
    ];
  },
});
