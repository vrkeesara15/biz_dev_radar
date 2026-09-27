"use client";

import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { errorMessage } from "@/lib/api/browser";
import { ApiError } from "@/lib/opportunities/api";
import { listIntegrations, putIntegration, type Integration } from "@/lib/settings/api";
import {
  INTEGRATIONS,
  secretStatus,
  toIntegrationBody,
  validateIntegration,
  type IntegrationForm,
  type IntegrationMeta,
  type SecretName,
} from "@/lib/settings/integrations";

const emptyForm = (row?: Integration): IntegrationForm => ({
  enabled: row?.enabled ?? false,
  config: Object.fromEntries(
    Object.entries(row?.config ?? {}).map(([key, value]) => [key, value === null || value === undefined ? "" : String(value)]),
  ),
  secrets: {},
  secretRef: "",
});

function IntegrationCard({
  meta,
  row,
  onSaved,
}: {
  meta: IntegrationMeta;
  row?: Integration;
  onSaved: (saved: Integration) => void;
}) {
  const [form, setForm] = React.useState<IntegrationForm>(() => emptyForm(row));
  const [saving, setSaving] = React.useState(false);
  const errors = validateIntegration(meta, form);
  const configured = Boolean(row?.secret_set);

  React.useEffect(() => {
    setForm(emptyForm(row));
  }, [row]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (Object.keys(errors).length) return;
    setSaving(true);
    try {
      const saved = await putIntegration(meta.kind, toIntegrationBody(meta, form));
      onSaved(saved);
      setForm((current) => ({ ...current, secrets: {}, secretRef: "" }));
      toast.success(`${meta.label} saved`);
    } catch (caught) {
      toast.error(
        caught instanceof ApiError
          ? errorMessage(caught.body, `Could not save ${meta.label} (${caught.status})`)
          : `Could not save ${meta.label}`,
      );
    } finally {
      setSaving(false);
    }
  };

  const setSecret = (name: SecretName, value: string) =>
    setForm((current) => ({ ...current, secrets: { ...current.secrets, [name]: value } }));

  return (
    <Card data-testid={`integration-${meta.kind}`}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>{meta.label}</span>
          <span className="flex items-center gap-2">
            <Badge variant="outline" data-testid={`secret-status-${meta.kind}`}>
              {secretStatus(row)}
            </Badge>
            {row?.enabled ? <Badge variant="outline">Enabled</Badge> : null}
          </span>
        </CardTitle>
        <CardDescription>{meta.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-4" onSubmit={submit}>
          <span className="flex items-center gap-2">
            <Checkbox
              id={`enabled-${meta.kind}`}
              checked={form.enabled}
              onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
            />
            <Label htmlFor={`enabled-${meta.kind}`} className="font-normal">
              Send notifications through {meta.label}
            </Label>
          </span>
          {errors.enabled ? (
            <p role="alert" className="text-xs text-destructive">
              {errors.enabled}
            </p>
          ) : null}

          {meta.config.length ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {meta.config.map((field) => {
                const id = `${meta.kind}-${field.name}`;
                const value = form.config[field.name] ?? "";
                return (
                  <div key={field.name} className="grid gap-1.5">
                    <Label htmlFor={id}>{field.label}</Label>
                    {field.options ? (
                      <NativeSelect
                        id={id}
                        value={value}
                        onChange={(event) =>
                          setForm({ ...form, config: { ...form.config, [field.name]: event.target.value } })
                        }
                      >
                        <option value="">Choose…</option>
                        {field.options.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </NativeSelect>
                    ) : (
                      <Input
                        id={id}
                        value={value}
                        placeholder={field.placeholder}
                        onChange={(event) =>
                          setForm({ ...form, config: { ...form.config, [field.name]: event.target.value } })
                        }
                      />
                    )}
                    {field.help ? <p className="text-xs text-muted-foreground">{field.help}</p> : null}
                  </div>
                );
              })}
            </div>
          ) : null}

          {meta.secrets.map((field) => {
            const id = `${meta.kind}-${field.name}`;
            return (
              <div key={field.name} className="grid gap-1.5">
                <Label htmlFor={id}>{field.label}</Label>
                <Input
                  id={id}
                  type="password"
                  autoComplete="off"
                  value={form.secrets[field.name] ?? ""}
                  placeholder={configured ? "Stored — type to replace" : ""}
                  aria-invalid={!!errors[field.name] || undefined}
                  onChange={(event) => setSecret(field.name, event.target.value)}
                />
                {errors[field.name] ? (
                  <p role="alert" className="text-xs text-destructive">
                    {errors[field.name]}
                  </p>
                ) : field.help ? (
                  <p className="text-xs text-muted-foreground">{field.help}</p>
                ) : null}
              </div>
            );
          })}

          <div className="grid gap-1.5">
            <Label htmlFor={`${meta.kind}-secret-ref`}>Secret reference (optional)</Label>
            <Input
              id={`${meta.kind}-secret-ref`}
              value={form.secretRef}
              placeholder="env:SLACK_WEBHOOK_URL or sm://projects/…/versions/1"
              aria-invalid={!!errors.secret_ref || undefined}
              onChange={(event) => setForm({ ...form, secretRef: event.target.value })}
            />
            {errors.secret_ref ? (
              <p role="alert" className="text-xs text-destructive">
                {errors.secret_ref}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Point at a secret the platform already holds instead of pasting one.
              </p>
            )}
          </div>

          {meta.note ? <p className="text-xs text-muted-foreground">{meta.note}</p> : null}
          {!meta.testable ? (
            <p className="text-xs text-muted-foreground">
              There is no test-connection call on the API yet; the first real alert is the test.
            </p>
          ) : null}

          <div>
            <Button type="submit" disabled={saving || Object.keys(errors).length > 0}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/** Settings > Integrations (SPEC 7 channels; owner only, as the API enforces). */
export function IntegrationsScreen() {
  const [rows, setRows] = React.useState<Integration[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    listIntegrations(controller.signal)
      .then((items) => setRows(items))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          caught instanceof ApiError && caught.status === 403
            ? "Only the tenant owner may manage integrations."
            : caught instanceof ApiError
              ? errorMessage(caught.body, `Integrations could not be read (${caught.status}).`)
              : "Integrations could not be read.",
        );
      });
    return () => controller.abort();
  }, []);

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (!rows) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const byKind = new Map(rows.map((row) => [row.kind, row]));

  return (
    <div className="grid gap-4" data-testid="integrations-screen">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Integrations</h2>
        <p className="text-sm text-muted-foreground">
          Where alerts and key dates go. Secrets are encrypted on save and never shown again.
        </p>
      </div>
      {INTEGRATIONS.map((meta) => (
        <IntegrationCard
          key={meta.kind}
          meta={meta}
          row={byKind.get(meta.kind)}
          onSaved={(saved) =>
            setRows((current) => {
              const others = (current ?? []).filter((row) => row.kind !== saved.kind);
              return [...others, saved];
            })
          }
        />
      ))}
    </div>
  );
}
