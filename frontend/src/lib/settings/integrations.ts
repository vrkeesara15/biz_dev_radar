/**
 * Integration cards (SPEC 7 channels, 10.4 screen 8).
 *
 * One table describes each connection: the plain `config` fields (stored as
 * jsonb and readable by every owner) and the secret fields (write-only; the
 * API encrypts them into `secret_ref` and never reads them back, so the UI can
 * only ever say "configured"). `PUT /integrations/{kind}` refuses a secret
 * hidden inside `config`, so the two lists must not overlap — a unit test
 * checks that.
 */
import type { IntegrationIn, IntegrationKind } from "./api";

export type ConfigField = {
  name: string;
  label: string;
  placeholder?: string;
  help?: string;
  /** Rendered as a select when present. */
  options?: readonly { value: string; label: string }[];
};

/** `webhook_url` and `signing_secret` are the only pasted secrets the API takes. */
export type SecretName = "webhook_url" | "signing_secret";

export type SecretField = { name: SecretName; label: string; help?: string };

export type IntegrationMeta = {
  kind: IntegrationKind;
  label: string;
  description: string;
  config: readonly ConfigField[];
  secrets: readonly SecretField[];
  /** Shown under the card when the platform side is not finished. */
  note?: string;
  /** True when the API exposes a test-connection call for this kind. */
  testable: boolean;
};

export const INTEGRATIONS: readonly IntegrationMeta[] = [
  {
    kind: "slack",
    label: "Slack",
    description: "Alerts in a channel, with Pursue / Pass / Assign buttons on the message.",
    config: [
      { name: "channel", label: "Channel", placeholder: "#bids", help: "Optional override for the webhook's own channel." },
      { name: "mention", label: "Mention", placeholder: "@here" },
    ],
    secrets: [
      { name: "webhook_url", label: "Incoming webhook URL", help: "https://hooks.slack.com/services/…" },
      {
        name: "signing_secret",
        label: "Signing secret",
        help: "From the Slack app's Basic Information. Needed for the interactive buttons.",
      },
    ],
    testable: false,
  },
  {
    kind: "teams",
    label: "Microsoft Teams",
    description: "Adaptive-card alerts posted to a channel webhook.",
    config: [{ name: "channel", label: "Channel name", placeholder: "Bids" }],
    secrets: [{ name: "webhook_url", label: "Incoming webhook URL", help: "https://outlook.office.com/webhook/…" }],
    testable: false,
  },
  {
    kind: "whatsapp",
    label: "WhatsApp (India)",
    description: "Deadline reminders through a Business API provider, using pre-approved templates.",
    config: [
      {
        name: "provider",
        label: "Provider (BSP)",
        options: [
          { value: "gupshup", label: "Gupshup" },
          { value: "twilio", label: "Twilio" },
        ],
      },
      { name: "sender", label: "Sender number", placeholder: "+91…" },
      { name: "template_high_fit", label: "Template — High-fit match", placeholder: "bidradar_high_fit" },
      { name: "template_reminder", label: "Template — deadline reminder", placeholder: "bidradar_deadline" },
    ],
    // The API's IntegrationIn has no `api_key` field, so the BSP credential can
    // only be pointed at, not pasted (OQ-93).
    secrets: [],
    note: "The provider credential is held by the platform: give its reference (env:NAME or sm://…) below.",
    testable: false,
  },
  {
    kind: "google_calendar",
    label: "Google Calendar",
    description: "Key dates written to a calendar your team already watches.",
    config: [{ name: "calendar_id", label: "Calendar ID", placeholder: "team@example.com" }],
    secrets: [],
    note: "OAuth consent is not wired up yet; give a service-account reference (env:NAME or sm://…) below.",
    testable: false,
  },
  {
    kind: "microsoft_calendar",
    label: "Outlook / Microsoft Calendar",
    description: "The same key dates in Microsoft 365.",
    config: [{ name: "calendar_id", label: "Calendar ID", placeholder: "AAMkAD…" }],
    secrets: [],
    note: "OAuth consent is not wired up yet; give an app-registration reference (env:NAME or sm://…) below.",
    testable: false,
  },
] as const;

export const integrationMeta = (kind: string): IntegrationMeta | undefined =>
  INTEGRATIONS.find((meta) => meta.kind === kind);

/** Schemes `secret_ref` accepts, per the API. */
export const SECRET_REF_PREFIXES = ["env:", "sm://", "enc:"] as const;

export const isSecretRef = (value: string): boolean =>
  SECRET_REF_PREFIXES.some((prefix) => value.trim().startsWith(prefix));

export type IntegrationForm = {
  enabled: boolean;
  config: Record<string, string>;
  /** Only the fields the person actually typed; blanks are never sent. */
  secrets: Partial<Record<SecretName, string>>;
  secretRef: string;
};

export type FormErrors = Partial<Record<string, string>>;

/** The checks the API makes, made here first so nothing round-trips to a 422. */
export function validateIntegration(meta: IntegrationMeta, form: IntegrationForm): FormErrors {
  const errors: FormErrors = {};
  const pasted = Object.entries(form.secrets).filter(([, value]) => (value ?? "").trim());
  const ref = form.secretRef.trim();
  if (pasted.length && ref) {
    errors.secret_ref = "Give either the secrets themselves or a reference, not both.";
  }
  if (ref && !isSecretRef(ref)) {
    errors.secret_ref = "A reference starts with env:, sm:// or enc:.";
  }
  for (const [name, value] of pasted) {
    if (name === "webhook_url" && !/^https:\/\//i.test((value ?? "").trim())) {
      errors.webhook_url = "The webhook URL must be https.";
    }
  }
  if (form.enabled && !ref && !pasted.length) {
    // Turning a connection on without any credential would fail silently at send time.
    const needs = meta.secrets.length ? meta.secrets[0].label.toLowerCase() : "credential reference";
    errors.enabled = `Add the ${needs} before enabling ${meta.label}.`;
  }
  return errors;
}

/** Body for PUT /integrations/{kind}: blanks dropped, secrets never echoed. */
export function toIntegrationBody(meta: IntegrationMeta, form: IntegrationForm): IntegrationIn {
  const config: Record<string, string> = {};
  for (const field of meta.config) {
    const value = (form.config[field.name] ?? "").trim();
    if (value) config[field.name] = value;
  }
  const body: IntegrationIn = { enabled: form.enabled, config };
  const ref = form.secretRef.trim();
  if (ref) {
    body.secret_ref = ref;
    return body;
  }
  for (const field of meta.secrets) {
    const value = (form.secrets[field.name] ?? "").trim();
    if (value) body[field.name] = value;
  }
  return body;
}

/** "Configured (encrypted)" / "Configured (env)" / "Not configured". */
export function secretStatus(row: { secret_set: boolean; secret_scheme: string | null } | undefined): string {
  if (!row?.secret_set) return "Not configured";
  const scheme = row.secret_scheme;
  if (scheme === "enc") return "Configured (encrypted here)";
  if (scheme === "env") return "Configured (environment)";
  if (scheme === "sm") return "Configured (Secret Manager)";
  return "Configured";
}
