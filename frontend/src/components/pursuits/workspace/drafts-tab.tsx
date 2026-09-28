"use client";

import { AlertTriangleIcon, BotIcon, FileSearchIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { DISCLAIMER } from "@/components/opportunities/attribution-footer";
import { CommentsThread } from "@/components/pursuits/comments-thread";
import { DraftEditor, type DraftEditorHandle } from "@/components/pursuits/workspace/draft-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import {
  asStaleVersion,
  needsInputChips,
  orphanNeedsInput,
  type DraftFlags,
  type GroundingFlag,
  type NeedsInputChip,
  type NeedsInputItem,
  type StaleVersion,
} from "@/lib/pursuits/drafts";
import { canApproveSection, canComment, canEditDrafts, canReadDrafts } from "@/lib/pursuits/roles";
import {
  approveDraft,
  getDraft,
  listDrafts,
  putDraft,
  type Draft,
  type DraftSummary,
} from "@/lib/pursuits/workspace-api";
import type { Role } from "@/types/next-auth";

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

const flagsOf = (draft: Draft | null): DraftFlags => (draft?.current?.flags ?? {}) as DraftFlags;
const groundingFlags = (draft: Draft | null): GroundingFlag[] => flagsOf(draft).flags ?? [];
const needsInputOf = (draft: Draft | null): NeedsInputItem[] =>
  ((draft?.current?.needs_input ?? []) as NeedsInputItem[]) ?? [];

type Citation = {
  token: string;
  source_type?: string;
  source_id?: string;
  page?: number | null;
  quote?: string;
};

const citationsOf = (draft: Draft | null): Citation[] =>
  ((draft?.current?.citations ?? []) as Citation[]) ?? [];

export function DraftsTab({
  pursuitId,
  role,
  onOpenTask,
  onChanged,
}: {
  pursuitId: string;
  role: Role | undefined;
  /** Jumps to the Tasks tab with the [NEEDS INPUT] task selected. */
  onOpenTask: (taskId: string) => void;
  /** The pursuit's draft counters moved; the header reloads. */
  onChanged?: () => void;
}) {
  const [sections, setSections] = React.useState<DraftSummary[] | null>(null);
  const [sectionId, setSectionId] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [listError, setListError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [dirty, setDirty] = React.useState(false);
  const [conflict, setConflict] = React.useState<StaleVersion | null>(null);
  const [mine, setMine] = React.useState<string | null>(null);
  const [showVersion, setShowVersion] = React.useState<number | null>(null);
  const [reloadToken, setReloadToken] = React.useState(0);
  const editorRef = React.useRef<DraftEditorHandle>(null);

  const mayRead = canReadDrafts(role);
  const mayEdit = canEditDrafts(role);
  const mayApprove = canApproveSection(role);

  React.useEffect(() => {
    if (!mayRead) return;
    const controller = new AbortController();
    listDrafts(pursuitId, controller.signal)
      .then((list) => {
        setSections(list.items ?? []);
        setSectionId((current) => current ?? list.items?.[0]?.section_id ?? null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setListError(describe(caught, "The draft sections could not be read"));
        setSections([]);
      });
    return () => controller.abort();
  }, [pursuitId, mayRead, reloadToken]);

  React.useEffect(() => {
    if (!sectionId || !mayRead) return;
    const controller = new AbortController();
    setError(null);
    getDraft(pursuitId, sectionId, controller.signal)
      .then((row) => {
        setDraft(row);
        setDirty(false);
        setConflict(null);
        setMine(null);
        setShowVersion(null);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setDraft(null);
        setError(describe(caught, "That section could not be read"));
      });
    return () => controller.abort();
  }, [pursuitId, sectionId, mayRead, reloadToken]);

  if (!mayRead) {
    return (
      <Card data-testid="drafts-forbidden">
        <CardHeader>
          <CardTitle>Draft text is not part of a read-only role</CardTitle>
          <CardDescription>
            SPEC 3 gives the viewer role read-only dashboards; drafts, comments and exports belong to
            the writer, reviewer, bid manager and tenant owner roles.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const version = draft?.current ?? null;
  const baseVersion = version?.version ?? 0;
  const bodyHtml = version?.body_html ?? "";
  const bodyText = version?.body_text ?? "";
  const agentAuthored = version?.author === "agent";
  const flags = groundingFlags(draft);
  const needsInput = needsInputOf(draft);
  const chips: NeedsInputChip[] = needsInputChips(bodyText, needsInput);
  const orphans = orphanNeedsInput(bodyText, needsInput);
  const redTeam = flagsOf(draft).red_team ?? null;
  const unsupported = flagsOf(draft).unsupported_count ?? flags.length;

  const save = async () => {
    if (!draft || !sectionId) return;
    const html = editorRef.current?.html() ?? bodyHtml;
    setBusy(true);
    setConflict(null);
    try {
      const saved = await putDraft(pursuitId, sectionId, {
        body_html: html,
        base_version: baseVersion,
      });
      setDraft(saved);
      setDirty(false);
      setMine(null);
      setReloadToken((token) => token + 1);
      onChanged?.();
      toast.success(`Saved version ${saved.current?.version ?? baseVersion + 1}`);
    } catch (caught) {
      const stale = asStaleVersion(caught);
      if (stale) {
        setMine(html);
        setConflict(stale);
      } else {
        toast.error(describe(caught, "That save was refused"));
      }
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!sectionId) return;
    setBusy(true);
    try {
      const approved = await approveDraft(pursuitId, sectionId);
      setDraft(approved);
      setReloadToken((token) => token + 1);
      onChanged?.();
      toast.success("Section approved");
    } catch (caught) {
      toast.error(describe(caught, "That section could not be approved"));
    } finally {
      setBusy(false);
    }
  };

  const reveal = (text: string) => {
    if (!editorRef.current?.reveal(text)) toast.message("That text is not in the current version.");
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[14rem_minmax(0,1fr)_20rem]" data-testid="drafts-tab">
      {/* left: the section list */}
      <nav aria-label="Draft sections" data-testid="section-list" className="grid content-start gap-1">
        {listError ? (
          <p role="alert" className="text-sm text-destructive">
            {listError}
          </p>
        ) : null}
        {sections?.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No sections yet. Run the outline and drafting agents from the header.
          </p>
        ) : null}
        {(sections ?? []).map((item) => {
          const active = item.section_id === sectionId;
          return (
            <button
              key={item.id}
              type="button"
              data-testid="section-row"
              aria-current={active ? "true" : undefined}
              onClick={() => setSectionId(item.section_id)}
              className={`grid gap-1 rounded-lg border px-2.5 py-2 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${
                active ? "border-primary bg-muted" : "hover:bg-muted/60"
              }`}
            >
              <span className="font-medium">{item.title}</span>
              <span className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                <Badge variant="outline" className="capitalize">
                  {item.status.replace(/_/g, " ")}
                </Badge>
                {item.unsupported_claims ? (
                  <Badge variant="destructive" data-testid="section-unsupported">
                    {item.unsupported_claims} unsupported
                  </Badge>
                ) : null}
                {item.needs_input ? <Badge variant="secondary">{item.needs_input} needs input</Badge> : null}
              </span>
            </button>
          );
        })}
      </nav>

      {/* centre: the editor */}
      <div className="grid content-start gap-3">
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        {draft ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold">{draft.title}</h2>
              {draft.volume ? <Badge variant="outline">{draft.volume}</Badge> : null}
              <Badge variant="secondary" className="capitalize">
                {draft.status.replace(/_/g, " ")}
              </Badge>
              {agentAuthored ? (
                <Badge data-testid="ai-draft-badge" className="gap-1">
                  <BotIcon className="size-3" aria-hidden="true" /> AI draft
                </Badge>
              ) : null}
              {unsupported ? (
                <Badge variant="destructive" data-testid="unsupported-count">
                  {unsupported} unsupported {unsupported === 1 ? "claim" : "claims"}
                </Badge>
              ) : null}
            </div>

            {agentAuthored ? (
              <p
                role="note"
                data-testid="ai-disclaimer"
                className="rounded-lg border border-amber-500/40 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
              >
                This section was written by an AI agent and is a draft. Every company fact must cite a
                record; sentences highlighted red are not grounded. {DISCLAIMER}
              </p>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" size="sm" onClick={() => void save()} disabled={!mayEdit || busy || !dirty}>
                Save
              </Button>
              {mayApprove ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="approve-section"
                  onClick={() => void approve()}
                  disabled={busy || draft.status === "approved"}
                >
                  {draft.status === "approved" ? "Approved" : "Approve section"}
                </Button>
              ) : null}
              <span className="text-xs text-muted-foreground" data-testid="base-version">
                Version {baseVersion}
                {version?.model ? ` · ${version.model}` : ""}
                {dirty ? " · unsaved changes" : ""}
              </span>
              <div className="ml-auto flex items-center gap-1.5">
                <Label htmlFor="version-history" className="text-xs">
                  History
                </Label>
                <NativeSelect
                  id="version-history"
                  data-testid="version-history"
                  className="w-28"
                  value={String(showVersion ?? baseVersion)}
                  onChange={(event) => setShowVersion(Number(event.target.value))}
                >
                  {(draft.versions ?? [baseVersion]).map((number) => (
                    <option key={number} value={number}>
                      Version {number}
                      {number === baseVersion ? " (current)" : ""}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            </div>

            {showVersion !== null && showVersion !== baseVersion ? (
              <p role="status" data-testid="version-note" className="text-xs text-muted-foreground">
                Version {showVersion} is stored, but the API serves only the current version
                ({baseVersion}) of a section, so its text cannot be shown here yet.
              </p>
            ) : null}

            {conflict ? (
              <Card role="alert" data-testid="save-conflict" className="border-destructive">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <AlertTriangleIcon className="size-4 text-destructive" aria-hidden="true" />
                    This section moved on while you were editing
                  </CardTitle>
                  <CardDescription>{conflict.message}</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-2">
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      size="sm"
                      data-testid="conflict-reload"
                      onClick={() => setReloadToken((token) => token + 1)}
                    >
                      Reload version {conflict.currentVersion ?? "on the server"}
                    </Button>
                  </div>
                  <details data-testid="conflict-diff">
                    <summary className="cursor-pointer text-xs text-muted-foreground">
                      Compare: your unsaved text and the version you started from
                    </summary>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      <div>
                        <p className="text-xs font-medium">Yours (unsaved)</p>
                        <pre className="max-h-48 overflow-auto rounded border bg-muted/40 p-2 text-xs whitespace-pre-wrap">
                          {mine ?? ""}
                        </pre>
                      </div>
                      <div>
                        <p className="text-xs font-medium">Version {baseVersion} you loaded</p>
                        <pre className="max-h-48 overflow-auto rounded border bg-muted/40 p-2 text-xs whitespace-pre-wrap">
                          {bodyHtml}
                        </pre>
                      </div>
                    </div>
                  </details>
                </CardContent>
              </Card>
            ) : null}

            <DraftEditor
              ref={editorRef}
              contentKey={`${draft.id}:${baseVersion}:${reloadToken}`}
              html={bodyHtml}
              editable={mayEdit}
              flags={flags}
              needsInput={needsInput}
              label={`${draft.title} draft body`}
              onDirtyChange={setDirty}
            />

            {chips.length || orphans.length ? (
              <div className="grid gap-1.5" data-testid="needs-input-chips">
                <p className="text-xs font-medium text-muted-foreground">
                  Needs input — an agent refused to invent these and opened a task for each.
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {chips.map((chip, index) => (
                    <li key={`${chip.from}-${index}`}>
                      <Button
                        type="button"
                        size="xs"
                        variant="outline"
                        data-testid="needs-input-chip"
                        onClick={() => (chip.taskId ? onOpenTask(chip.taskId) : reveal(chip.marker))}
                        title={chip.question ?? undefined}
                      >
                        {chip.label || "unspecified input"}
                        {chip.taskId ? " → task" : ""}
                      </Button>
                    </li>
                  ))}
                  {orphans.map((item, index) => (
                    <li key={`orphan-${index}`}>
                      <Button
                        type="button"
                        size="xs"
                        variant="ghost"
                        data-testid="needs-input-orphan"
                        onClick={() => item.task_id && onOpenTask(item.task_id)}
                        title={item.question ?? undefined}
                      >
                        {item.placeholder} (answered in the text)
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
      </div>

      {/* right: citations, red team, comments */}
      <div className="grid content-start gap-4">
        <Card data-testid="citations-panel">
          <CardHeader>
            <CardTitle className="text-sm">Citations</CardTitle>
            <CardDescription>
              Every company fact cites the record it came from (SPEC 8 grounding).
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2">
            {citationsOf(draft).length === 0 ? (
              <p className="text-sm text-muted-foreground">No citations on this version.</p>
            ) : null}
            {citationsOf(draft).map((citation) => (
              <div key={citation.token} data-testid="citation-row" className="grid gap-1 rounded-lg border p-2">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <Badge variant="outline" className="capitalize">
                    {(citation.source_type ?? "record").replace(/_/g, " ")}
                  </Badge>
                  {citation.page ? <span className="text-muted-foreground">page {citation.page}</span> : null}
                </div>
                {citation.quote ? (
                  <blockquote className="border-l-2 pl-2 text-xs text-muted-foreground">
                    {citation.quote}
                  </blockquote>
                ) : null}
                <div>
                  <Button
                    type="button"
                    size="xs"
                    variant="ghost"
                    data-testid="citation-show"
                    onClick={() => reveal(`[${citation.token}]`)}
                  >
                    <FileSearchIcon className="size-3" aria-hidden="true" /> Show in text
                  </Button>
                </div>
              </div>
            ))}
            {(flagsOf(draft).unresolved_tokens ?? []).length ? (
              <p className="text-xs text-destructive" data-testid="unresolved-tokens">
                {(flagsOf(draft).unresolved_tokens ?? []).length} cited record(s) could not be resolved.
              </p>
            ) : null}
          </CardContent>
        </Card>

        {redTeam ? (
          <Card data-testid="red-team-panel">
            <CardHeader>
              <CardTitle className="text-sm">Red team</CardTitle>
              <CardDescription>
                {redTeam.revised
                  ? "This version is the reviewer's one auto-revision; the issues below remain."
                  : "Findings from agent 8."}
                {redTeam.page_estimate?.over ? " Over the page limit." : ""}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2">
              {(redTeam.issues ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No open findings.</p>
              ) : null}
              {(redTeam.issues ?? []).map((issue, index) => (
                <div key={index} data-testid="red-team-issue" className="grid gap-1 rounded-lg border p-2 text-xs">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="destructive" className="capitalize">
                      {issue.kind.replace(/_/g, " ")}
                    </Badge>
                    {issue.requirement_id ? <span className="font-mono">{issue.requirement_id}</span> : null}
                  </div>
                  {issue.sentence ? (
                    <button
                      type="button"
                      className="text-left underline-offset-4 hover:underline"
                      onClick={() => reveal(issue.sentence ?? "")}
                    >
                      “{issue.sentence}”
                    </button>
                  ) : null}
                  <p className="text-muted-foreground">{issue.fix_suggestion}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}

        {canComment(role) && draft ? (
          <CommentsThread
            key={draft.id}
            pursuitId={pursuitId}
            targetType="draft_section"
            targetId={draft.id}
          />
        ) : null}
      </div>
    </div>
  );
}
