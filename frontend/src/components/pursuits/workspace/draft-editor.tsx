"use client";

import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import * as React from "react";

import { FlagHighlight, flagHighlightKey } from "@/components/pursuits/workspace/flag-highlight";
import type { GroundingFlag, NeedsInputItem } from "@/lib/pursuits/drafts";

export type DraftEditorHandle = {
  /** The current HTML, for the save button. */
  html: () => string;
  /** Scroll the first occurrence of `text` into view and select it. */
  reveal: (text: string) => boolean;
};

/**
 * The TipTap editor bound to one draft version's `body_html`.
 *
 * The body is the agent's or the writer's HTML, already allowlist-sanitised
 * by the backend (core.markdown.sanitize_html) on the way in and on the way
 * out, so it is rendered as-is; the grounding flags and the [NEEDS INPUT]
 * markers are painted by decorations, never by rewriting the text.
 */
export const DraftEditor = React.forwardRef<
  DraftEditorHandle,
  {
    /** Changing this remounts the content (a different section or version). */
    contentKey: string;
    html: string;
    editable: boolean;
    flags: GroundingFlag[];
    needsInput: NeedsInputItem[];
    label: string;
    onDirtyChange?: (dirty: boolean) => void;
  }
>(function DraftEditor(
  { contentKey, html, editable, flags, needsInput, label, onDirtyChange },
  ref,
) {
  // The plugin reads the live flags through this ref, so a new version's
  // report repaints the decorations without remounting the editor.
  const highlight = React.useRef({ flags, needsInput });
  highlight.current = { flags, needsInput };

  const editor = useEditor(
    {
      immediatelyRender: false,
      editable,
      extensions: [StarterKit, FlagHighlight.configure({ source: () => highlight.current })],
      content: html,
      editorProps: {
        attributes: {
          role: "textbox",
          "aria-multiline": "true",
          "aria-label": label,
          "aria-readonly": editable ? "false" : "true",
          class: "text-sm leading-relaxed",
          "data-testid": "draft-body",
        },
      },
      onUpdate: () => onDirtyChange?.(true),
    },
    [contentKey],
  );

  // A new version's flags must repaint even when the text is unchanged.
  React.useEffect(() => {
    if (!editor) return;
    editor.view.dispatch(editor.state.tr.setMeta(flagHighlightKey, true));
  }, [editor, flags, needsInput]);

  React.useEffect(() => {
    editor?.setEditable(editable);
  }, [editor, editable]);

  React.useImperativeHandle(
    ref,
    () => ({
      html: () => editor?.getHTML() ?? html,
      reveal: (text: string) => {
        if (!editor) return false;
        const needle = text.trim();
        if (!needle) return false;
        let found: { from: number; to: number } | null = null;
        editor.state.doc.descendants((node, pos) => {
          if (found || !node.isTextblock) return !found;
          const index = node.textContent.indexOf(needle);
          if (index >= 0) found = { from: pos + 1 + index, to: pos + 1 + index + needle.length };
          return false;
        });
        if (!found) return false;
        const { from, to } = found;
        editor.chain().focus().setTextSelection({ from, to }).scrollIntoView().run();
        return true;
      },
    }),
    [editor, html],
  );

  return (
    <div className="bidradar-editor rounded-lg border bg-background p-3" data-testid="draft-editor">
      <EditorContent editor={editor} />
    </div>
  );
});
