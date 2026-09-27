"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Profile } from "@/lib/api/browser";
import {
  applySuggestion,
  formatSuggestionValue,
  groupSuggestionsByStep,
  HIGH_CONFIDENCE,
  highConfidenceIndexes,
  isSuggestionAllowed,
  requestAutofill,
  SOURCE_LABELS,
  suggestionLabel,
  type ApplyOutcome,
  type AutofillRequest,
  type AutofillSuggestion,
} from "@/lib/autofill";
import { createItem, uploadFile, type UploadedFile } from "@/lib/onboarding/api";
import { fromApiRegion, STEPS, type Region } from "@/lib/profile-fields";

import { Field, controlProps } from "./form";

type RowState = "pending" | "applying" | "applied" | "rejected" | "error";

export function AutofillPanel({
  region,
  profile,
  ensureProfile,
  onApplied,
  websiteDefault,
  ueiDefault,
}: {
  region: Region;
  profile: Profile | null;
  /** Creates the draft profile when needed (the endpoint is profile-scoped). */
  ensureProfile: () => Promise<Profile>;
  onApplied: (suggestion: AutofillSuggestion, outcome: ApplyOutcome) => void;
  websiteDefault?: string;
  ueiDefault?: string;
}) {
  const [website, setWebsite] = React.useState(websiteDefault ?? "");
  const [uei, setUei] = React.useState(ueiDefault ?? "");
  const [file, setFile] = React.useState<UploadedFile | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [running, setRunning] = React.useState(false);
  const [notice, setNotice] = React.useState<{ kind: "info" | "error"; text: string } | null>(null);
  const [warnings, setWarnings] = React.useState<string[]>([]);
  const [suggestions, setSuggestions] = React.useState<AutofillSuggestion[]>([]);
  const [rows, setRows] = React.useState<Record<number, { state: RowState; message?: string }>>({});

  React.useEffect(() => {
    if (websiteDefault && !website) setWebsite(websiteDefault);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [websiteDefault]);
  React.useEffect(() => {
    if (ueiDefault && !uei) setUei(ueiDefault);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ueiDefault]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    if (!picked) return;
    setUploading(true);
    setNotice(null);
    try {
      setFile(await uploadFile(picked));
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Upload failed" });
    } finally {
      setUploading(false);
    }
  };

  const run = async () => {
    const request: AutofillRequest = {};
    if (website.trim()) request.website_url = website.trim();
    if (file) request.capability_file_id = file.id;
    if (region === "US" && uei.trim()) request.uei = uei.trim().toUpperCase();
    if (!Object.keys(request).length) {
      setNotice({ kind: "error", text: "Enter a website, upload a capability statement or give a UEI first." });
      return;
    }
    setRunning(true);
    setNotice(null);
    setSuggestions([]);
    setRows({});
    setWarnings([]);
    try {
      const target = await ensureProfile();
      if (file) {
        // Keep the uploaded statement on the profile (RAG source) – best effort.
        createItem(target.id, "files", {
          file_id: file.id,
          kind: "capability_statement",
          title: file.filename,
          meta: {},
        }).catch(() => undefined);
      }
      const result = await requestAutofill(target.id, request);
      if (result.status === "unavailable") {
        setNotice({
          kind: "info",
          text: "Autofill is not available yet on this server. You can fill the profile in manually.",
        });
        return;
      }
      if (result.status === "error") {
        setNotice({ kind: "error", text: result.message });
        return;
      }
      const allowed = result.data.suggestions.filter((s) => isSuggestionAllowed(s, region));
      const hidden = result.data.suggestions.length - allowed.length;
      setSuggestions(allowed);
      setWarnings([
        ...result.data.warnings,
        ...(hidden ? [`${hidden} suggestion${hidden > 1 ? "s" : ""} for the other region hidden.`] : []),
      ]);
      if (!allowed.length) setNotice({ kind: "info", text: "No suggestions found." });
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Autofill failed" });
    } finally {
      setRunning(false);
    }
  };

  const accept = async (index: number) => {
    const s = suggestions[index];
    if (!s) return;
    setRows((r) => ({ ...r, [index]: { state: "applying" } }));
    try {
      const target = await ensureProfile();
      const outcome = await applySuggestion(
        { profileId: target.id, region: fromApiRegion(target.region), profile: target },
        s,
      );
      setRows((r) => ({ ...r, [index]: { state: "applied" } }));
      onApplied(s, outcome);
    } catch (err) {
      setRows((r) => ({
        ...r,
        [index]: { state: "error", message: err instanceof Error ? err.message : "Could not apply" },
      }));
    }
  };

  const reject = (index: number) => setRows((r) => ({ ...r, [index]: { state: "rejected" } }));

  const acceptHighConfidence = async () => {
    for (const index of highConfidenceIndexes(suggestions)) {
      const state = rows[index]?.state ?? "pending";
      if (state === "pending" || state === "error") await accept(index);
    }
  };

  const groups = groupSuggestionsByStep(suggestions);
  const pendingHigh = highConfidenceIndexes(suggestions).filter(
    (i) => (rows[i]?.state ?? "pending") === "pending" || rows[i]?.state === "error",
  ).length;

  return (
    <section
      aria-labelledby="autofill-heading"
      data-testid="autofill-panel"
      className="grid gap-4 rounded-xl border border-dashed bg-muted/30 p-5"
    >
      <div>
        <h2 id="autofill-heading" className="text-sm font-semibold">
          Autofill from your website, capability statement{region === "US" ? " or SAM.gov" : ""}
        </h2>
        <p className="text-sm text-muted-foreground">
          We read public sources and suggest values. Nothing is saved until you accept a suggestion.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field id="autofill-website" label="Website URL">
          <Input
            {...controlProps("autofill-website")}
            type="url"
            inputMode="url"
            placeholder="https://www.example.com"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
          />
        </Field>
        <Field
          id="autofill-file"
          label="Capability statement (PDF)"
          help={file ? `Uploaded: ${file.filename}` : uploading ? "Uploading…" : undefined}
        >
          <Input
            {...controlProps("autofill-file", undefined, file || uploading ? "x" : undefined)}
            type="file"
            accept=".pdf,.docx,.doc"
            onChange={handleUpload}
            disabled={uploading}
          />
        </Field>
        {region === "US" ? (
          <Field id="autofill-uei" label="UEI" help="12-character SAM Unique Entity ID">
            <Input
              {...controlProps("autofill-uei", undefined, "x")}
              value={uei}
              maxLength={12}
              autoCapitalize="characters"
              onChange={(e) => setUei(e.target.value)}
            />
          </Field>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="secondary" onClick={run} disabled={running || uploading}>
          {running ? "Looking up…" : "Get suggestions"}
        </Button>
        {suggestions.length ? (
          <Button type="button" variant="outline" onClick={acceptHighConfidence} disabled={pendingHigh === 0}>
            Accept all high-confidence (≥ {HIGH_CONFIDENCE.toFixed(1)})
          </Button>
        ) : null}
        {!profile ? (
          <span className="text-xs text-muted-foreground">Requires a legal name; a draft profile is created first.</span>
        ) : null}
      </div>
      {notice ? (
        <p
          role={notice.kind === "error" ? "alert" : "status"}
          data-testid="autofill-notice"
          className={
            notice.kind === "error"
              ? "rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
              : "rounded-lg border px-3 py-2 text-sm text-muted-foreground"
          }
        >
          {notice.text}
        </p>
      ) : null}
      {warnings.length ? (
        <ul className="grid gap-1 text-xs text-muted-foreground">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {[...groups.entries()].map(([step, items]) => (
        <div key={step} className="grid gap-2" data-testid={`autofill-group-${step}`}>
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Step {step} · {STEPS[step - 1].title}
          </h3>
          <ul className="grid gap-2">
            {items.map((s) => {
              const row = rows[s.index] ?? { state: "pending" as RowState };
              const done = row.state === "applied" || row.state === "rejected";
              return (
                <li
                  key={s.index}
                  data-testid="autofill-suggestion"
                  data-state={row.state}
                  className="flex flex-wrap items-start justify-between gap-3 rounded-lg border bg-background p-3"
                >
                  <div className="grid min-w-0 flex-1 gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{suggestionLabel(s)}</span>
                      <Badge variant="outline" title={s.source_ref}>
                        {SOURCE_LABELS[s.source] ?? s.source}
                      </Badge>
                      <Badge
                        variant={s.confidence >= HIGH_CONFIDENCE ? "secondary" : "outline"}
                        aria-label={`Confidence ${Math.round(s.confidence * 100)} percent`}
                      >
                        {Math.round(s.confidence * 100)}%
                      </Badge>
                      {row.state === "applied" ? <Badge>Applied</Badge> : null}
                      {row.state === "rejected" ? <Badge variant="ghost">Rejected</Badge> : null}
                    </div>
                    <p className="text-sm break-words text-muted-foreground">{formatSuggestionValue(s.value)}</p>
                    {row.state === "error" ? (
                      <p role="alert" className="text-xs text-destructive">
                        {row.message}
                      </p>
                    ) : null}
                  </div>
                  {!done ? (
                    <div className="flex shrink-0 gap-2">
                      <Button
                        type="button"
                        size="sm"
                        onClick={() => accept(s.index)}
                        disabled={row.state === "applying"}
                        aria-label={`Accept ${suggestionLabel(s)}`}
                      >
                        {row.state === "applying" ? "Applying…" : "Accept"}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => reject(s.index)}
                        aria-label={`Reject ${suggestionLabel(s)}`}
                      >
                        Reject
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}
