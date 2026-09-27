import { describe, expect, it } from "vitest";

import {
  INTEGRATIONS,
  integrationMeta,
  isSecretRef,
  secretStatus,
  toIntegrationBody,
  validateIntegration,
  type IntegrationForm,
} from "./integrations";

const slack = integrationMeta("slack")!;
const whatsapp = integrationMeta("whatsapp")!;

const form = (overrides: Partial<IntegrationForm> = {}): IntegrationForm => ({
  enabled: false,
  config: {},
  secrets: {},
  secretRef: "",
  ...overrides,
});

describe("the integration table", () => {
  it("covers every channel the API knows", () => {
    expect(INTEGRATIONS.map((meta) => meta.kind)).toEqual([
      "slack",
      "teams",
      "whatsapp",
      "google_calendar",
      "microsoft_calendar",
    ]);
  });

  it("never puts a secret field name into config, which the API refuses", () => {
    const secretNames = new Set(["webhook_url", "signing_secret", "api_key"]);
    for (const meta of INTEGRATIONS) {
      for (const field of meta.config) {
        expect(secretNames.has(field.name), `${meta.kind}.${field.name}`).toBe(false);
      }
    }
  });
});

describe("validateIntegration", () => {
  it("accepts a pasted Slack webhook and signing secret", () => {
    const errors = validateIntegration(
      slack,
      form({ enabled: true, secrets: { webhook_url: "https://hooks.slack.com/services/T/B/x", signing_secret: "abc" } }),
    );
    expect(errors).toEqual({});
  });

  it("refuses a non-https webhook", () => {
    const errors = validateIntegration(slack, form({ secrets: { webhook_url: "http://hooks.slack.com/x" } }));
    expect(errors.webhook_url).toMatch(/https/);
  });

  it("refuses secrets and a reference at the same time, as the API does", () => {
    const errors = validateIntegration(
      slack,
      form({ secrets: { webhook_url: "https://hooks.slack.com/x" }, secretRef: "env:SLACK" }),
    );
    expect(errors.secret_ref).toMatch(/either/i);
  });

  it("requires a reference to name a scheme", () => {
    expect(validateIntegration(slack, form({ secretRef: "just-a-string" })).secret_ref).toMatch(/env:/);
    expect(validateIntegration(slack, form({ secretRef: "sm://projects/p/secrets/s/versions/1" })).secret_ref).toBeUndefined();
    expect(isSecretRef("enc:abc")).toBe(true);
    expect(isSecretRef("plain")).toBe(false);
  });

  it("will not enable a connection that has no credential at all", () => {
    expect(validateIntegration(slack, form({ enabled: true })).enabled).toMatch(/before enabling/i);
    expect(validateIntegration(whatsapp, form({ enabled: true, secretRef: "env:GUPSHUP_KEY" })).enabled).toBeUndefined();
  });
});

describe("toIntegrationBody", () => {
  it("sends the config, drops the blanks and encrypts nothing client-side", () => {
    const body = toIntegrationBody(
      slack,
      form({
        enabled: true,
        config: { channel: " #bids ", mention: "" },
        secrets: { webhook_url: " https://hooks.slack.com/services/T/B/x ", signing_secret: "" },
      }),
    );
    expect(body).toEqual({
      enabled: true,
      config: { channel: "#bids" },
      webhook_url: "https://hooks.slack.com/services/T/B/x",
    });
  });

  it("prefers a reference and then sends no pasted secret", () => {
    const body = toIntegrationBody(slack, form({ enabled: true, secretRef: " env:SLACK_WEBHOOK " }));
    expect(body).toEqual({ enabled: true, config: {}, secret_ref: "env:SLACK_WEBHOOK" });
  });

  it("carries the WhatsApp provider and template names as config", () => {
    const body = toIntegrationBody(
      whatsapp,
      form({
        enabled: true,
        config: { provider: "gupshup", sender: "+919000000000", template_high_fit: "bidradar_high_fit" },
        secretRef: "env:GUPSHUP_KEY",
      }),
    );
    expect(body.config).toEqual({
      provider: "gupshup",
      sender: "+919000000000",
      template_high_fit: "bidradar_high_fit",
    });
    expect(body.secret_ref).toBe("env:GUPSHUP_KEY");
  });
});

describe("secretStatus", () => {
  it("says how the secret is held, never what it is", () => {
    expect(secretStatus(undefined)).toBe("Not configured");
    expect(secretStatus({ secret_set: false, secret_scheme: null })).toBe("Not configured");
    expect(secretStatus({ secret_set: true, secret_scheme: "enc" })).toBe("Configured (encrypted here)");
    expect(secretStatus({ secret_set: true, secret_scheme: "env" })).toBe("Configured (environment)");
    expect(secretStatus({ secret_set: true, secret_scheme: "sm" })).toBe("Configured (Secret Manager)");
    expect(secretStatus({ secret_set: true, secret_scheme: "who-knows" })).toBe("Configured");
  });
});
