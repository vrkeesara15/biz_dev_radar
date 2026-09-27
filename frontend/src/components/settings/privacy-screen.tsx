"use client";

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import {
  deleteTenant,
  exportTenant,
  listConsents,
  listDataRequests,
  openDataRequest,
  getPrivacy,
  recordConsent,
  type Consent,
  type DataRequest,
  type Privacy,
} from "@/lib/settings/api";
import { canManageTenantData } from "@/lib/settings/roles";
import type { Role } from "@/types/next-auth";

const REQUEST_KINDS = [
  { value: "access", label: "Access — a copy of my data" },
  { value: "correction", label: "Correction — fix something wrong" },
  { value: "erasure", label: "Erasure — delete my personal data" },
] as const;

const CONSENTS: { kind: "dpdp" | "privacy_policy" | "terms"; label: string; versionKey: keyof Privacy }[] = [
  { kind: "dpdp", label: "DPDP notice (India)", versionKey: "dpdp_notice_version" },
  { kind: "privacy_policy", label: "Privacy policy", versionKey: "privacy_policy_version" },
  { kind: "terms", label: "Terms of service", versionKey: "terms_version" },
];

const KIND_LABELS: Record<string, string> = {
  access: "Access",
  correction: "Correction",
  erasure: "Erasure",
  tenant_export: "Tenant export",
  tenant_delete: "Tenant erasure",
};

const describe = (caught: unknown, fallback: string) =>
  caught instanceof ApiError ? errorMessage(caught.body, `${fallback} (${caught.status}).`) : fallback;

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "—");

/** Settings > Data and privacy (SPEC 11, 10.4 screen 8). */
export function PrivacyScreen({ role }: { role?: Role }) {
  const owner = canManageTenantData(role);
  const [privacy, setPrivacy] = React.useState<Privacy | null>(null);
  const [consents, setConsents] = React.useState<Consent[]>([]);
  const [requests, setRequests] = React.useState<DataRequest[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [kind, setKind] = React.useState<string>("access");
  const [note, setNote] = React.useState("");
  const [confirmKind, setConfirmKind] = React.useState<"export" | "delete" | null>(null);
  const [confirmText, setConfirmText] = React.useState("");

  React.useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const [policy, accepted, raised] = await Promise.all([
          getPrivacy(controller.signal),
          listConsents(controller.signal),
          listDataRequests(controller.signal),
        ]);
        setPrivacy(policy);
        setConsents(accepted);
        setRequests(raised);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setError(describe(caught, "The privacy settings could not be read"));
      }
    })();
    return () => controller.abort();
  }, []);

  const accept = async (consentKind: "dpdp" | "privacy_policy" | "terms", version: string) => {
    setBusy(consentKind);
    try {
      const created = await recordConsent({ kind: consentKind, version });
      setConsents((list) => [...list.filter((row) => row.kind !== consentKind), created]);
      toast.success("Recorded");
    } catch (caught) {
      toast.error(describe(caught, "Could not record the acceptance"));
    } finally {
      setBusy(null);
    }
  };

  const raise = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("request");
    try {
      const created = await openDataRequest({
        kind: kind as DataRequest["kind"],
        note: note.trim() || null,
      });
      setRequests((list) => [created, ...list]);
      setNote("");
      toast.success(
        created.status === "done"
          ? "Your data is attached to the request below"
          : `Request raised — we answer by ${day(created.sla_due_at)}`,
      );
    } catch (caught) {
      toast.error(describe(caught, "Could not raise the request"));
    } finally {
      setBusy(null);
    }
  };

  const runTenantJob = async () => {
    if (!confirmKind) return;
    setBusy("tenant");
    try {
      const job = confirmKind === "export" ? await exportTenant() : await deleteTenant();
      setConfirmKind(null);
      setConfirmText("");
      toast.success(
        confirmKind === "export"
          ? `Export started (${job.scheduling}); you will get the archive when it is ready`
          : `Erasure started (${job.scheduling}). The audit trail is kept, as the law requires`,
      );
      setRequests((list) => [
        {
          id: job.request_id,
          kind: job.kind,
          status: job.status,
          created_at: new Date().toISOString(),
          sla_due_at: job.sla_due_at,
          completed_at: null,
          overdue: false,
          details: job.details,
          result_file_id: job.result_file_id,
          data: null,
        },
        ...list,
      ]);
    } catch (caught) {
      toast.error(describe(caught, "Could not start the job"));
    } finally {
      setBusy(null);
    }
  };

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (!privacy) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const confirmWord = confirmKind === "delete" ? "DELETE" : "EXPORT";

  return (
    <div className="grid gap-6" data-testid="privacy-screen">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Data and privacy</h2>
        <p className="text-sm text-muted-foreground">
          What you have accepted, what you can ask us for, and who processes your data.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Consent</CardTitle>
          <CardDescription>
            We record the version you accepted and when. Accepting again re-records the current one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2" data-testid="consents">
            {CONSENTS.map((entry) => {
              const version = String(privacy[entry.versionKey] ?? "");
              const accepted = consents.find((row) => row.kind === entry.kind);
              const current = accepted?.version === version;
              return (
                <li key={entry.kind} className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    <span className="font-medium">{entry.label}</span>{" "}
                    <span className="text-xs text-muted-foreground">v{version}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <Badge variant="outline" data-testid={`consent-${entry.kind}`}>
                      {accepted ? (current ? `Accepted ${day(accepted.accepted_at)}` : `v${accepted.version} — out of date`) : "Not accepted"}
                    </Badge>
                    {!current ? (
                      <Button
                        type="button"
                        size="xs"
                        variant="outline"
                        disabled={busy === entry.kind}
                        onClick={() => void accept(entry.kind, version)}
                      >
                        Accept v{version}
                      </Button>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your data requests</CardTitle>
          <CardDescription>
            Access, correction and erasure of your own personal data. We answer within{" "}
            {privacy.data_request_sla_days} days.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <form className="flex flex-wrap items-end gap-3" onSubmit={raise}>
            <div className="grid gap-1.5">
              <Label htmlFor="request-kind">Request</Label>
              <NativeSelect
                id="request-kind"
                className="w-72"
                value={kind}
                onChange={(event) => setKind(event.target.value)}
              >
                {REQUEST_KINDS.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="grid min-w-64 flex-1 gap-1.5">
              <Label htmlFor="request-note">Note (optional)</Label>
              <Textarea
                id="request-note"
                rows={2}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="What should we correct, or what are you looking for?"
              />
            </div>
            <Button type="submit" disabled={busy === "request"}>
              Raise request
            </Button>
          </form>

          <div className="rounded-xl border">
            <Table data-testid="data-requests">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Kind</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Raised</TableHead>
                  <TableHead>Answer due</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.map((row) => (
                  <TableRow key={row.id} data-testid="data-request-row" data-kind={row.kind}>
                    <TableCell>{KIND_LABELS[row.kind] ?? row.kind}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{row.status.replace(/_/g, " ")}</Badge>
                      {row.overdue ? (
                        <Badge variant="outline" className="ml-1 text-destructive">
                          overdue
                        </Badge>
                      ) : null}
                    </TableCell>
                    <TableCell className="tabular-nums">{day(row.created_at)}</TableCell>
                    <TableCell className="tabular-nums">{day(row.sla_due_at)}</TableCell>
                  </TableRow>
                ))}
                {!requests.length ? (
                  <TableRow>
                    <TableCell colSpan={4} className="py-6 text-center text-muted-foreground">
                      No requests yet.
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {owner ? (
        <Card data-testid="tenant-data-card">
          <CardHeader>
            <CardTitle>Tenant export and erasure</CardTitle>
            <CardDescription>
              Everything this tenant holds: profiles, opportunities you tracked, drafts and uploads.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setConfirmKind("export");
                setConfirmText("");
              }}
            >
              Export everything
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                setConfirmKind("delete");
                setConfirmText("");
              }}
            >
              Delete this tenant
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Who to contact, and who processes your data</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm">
          <p data-testid="grievance-officer">
            <span className="font-medium">Grievance officer: </span>
            {privacy.grievance_officer.name ?? "Not named yet"}
            {privacy.grievance_officer.email ? (
              <>
                {" — "}
                <a href={`mailto:${privacy.grievance_officer.email}`} className="underline underline-offset-4">
                  {privacy.grievance_officer.email}
                </a>
              </>
            ) : null}
          </p>
          <div>
            <p className="font-medium">Sub-processors</p>
            <ul className="mt-1 grid gap-1" data-testid="sub-processors">
              {privacy.sub_processors.map((processor, index) => (
                <li key={`${processor.name ?? index}`} className="text-muted-foreground">
                  {[processor.name, processor.purpose, processor.region].filter(Boolean).join(" · ")}
                </li>
              ))}
              {!privacy.sub_processors.length ? (
                <li className="text-muted-foreground">None published.</li>
              ) : null}
            </ul>
          </div>
        </CardContent>
      </Card>

      <Dialog open={confirmKind !== null} onOpenChange={(open) => (open ? null : setConfirmKind(null))}>
        <DialogContent data-testid="tenant-confirm-dialog">
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (confirmText.trim() === confirmWord) void runTenantJob();
            }}
          >
            <DialogHeader>
              <DialogTitle>
                {confirmKind === "delete" ? "Delete this tenant" : "Export this tenant"}
              </DialogTitle>
              <DialogDescription>
                {confirmKind === "delete"
                  ? "Every row and file is erased. The audit trail stays, as the law requires. This cannot be undone."
                  : "We build a zip of every table and uploaded file and let you know when it is ready."}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-1.5">
              <Label htmlFor="tenant-confirm">
                Type {confirmWord} to confirm
              </Label>
              <Input
                id="tenant-confirm"
                value={confirmText}
                onChange={(event) => setConfirmText(event.target.value)}
                autoComplete="off"
                autoFocus
              />
            </div>
            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>Cancel</DialogClose>
              <Button
                type="submit"
                variant={confirmKind === "delete" ? "destructive" : "default"}
                disabled={busy === "tenant" || confirmText.trim() !== confirmWord}
              >
                {confirmKind === "delete" ? "Delete everything" : "Start export"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
