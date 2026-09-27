import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("the owner sees every settings tab and a writer only their own", async ({ page, context, baseURL }) => {
  const api = new MockApi({ members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings");
  await expect(page).toHaveURL(/\/app\/settings\/profile$/);
  const nav = page.getByRole("navigation", { name: "Settings sections" });
  await expect(nav.getByRole("link")).toHaveText([
    "Profile",
    "Users & roles",
    "Notifications",
    "Saved searches",
    "Integrations",
    "Billing",
    "Data & privacy",
  ]);

  // Profile links back into each wizard step (SPEC 4).
  await expect(page.getByTestId("profile-tab")).toBeVisible();

  // A writer is shown only what SPEC 3 lets them do, and the owner-only routes
  // send them back rather than 403-ing in the browser.
  await signInAs(context, baseURL!, { role: "writer" });
  await page.goto("/app/settings/profile");
  await expect(nav.getByRole("link")).toHaveText([
    "Profile",
    "Notifications",
    "Saved searches",
    "Data & privacy",
  ]);
  await page.goto("/app/settings/billing");
  await expect(page).toHaveURL(/\/app\/settings\/profile$/);
  await page.goto("/app/settings/users");
  await expect(page).toHaveURL(/\/app\/settings\/profile$/);
});

test("users and roles: the owner changes a role and invites a colleague", async ({ page, context, baseURL }) => {
  const api = new MockApi({ members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings/users");
  await expect(page.getByTestId("member-row")).toHaveCount(2);

  // The only owner cannot be demoted out of the tenant.
  await expect(page.getByLabel("Role for e2e@example.com")).toBeDisabled();

  await page.getByLabel("Role for writer@example.com").selectOption("bid_manager");
  await expect(page.getByText("writer@example.com is now Bid manager")).toBeVisible();
  const patch = api.requests.find((r) => r.method === "PATCH" && r.path === "/api/v1/tenant/members/mem-2");
  expect(patch?.body).toEqual({ role: "bid_manager" });

  await page.getByRole("button", { name: "Invite member" }).click();
  const dialog = page.getByTestId("invite-dialog");
  await dialog.getByLabel("Email").fill("reviewer@example.com");
  await dialog.getByLabel("Role").selectOption("reviewer");
  await expect(dialog).toContainText("Comments on and approves sections only.");
  await dialog.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText("Invited reviewer@example.com as Reviewer")).toBeVisible();
  expect(api.members.at(-1)).toMatchObject({ email: "reviewer@example.com", role: "reviewer" });
  await expect(page.getByTestId("member-row")).toHaveCount(3);

  // All six SPEC 3 roles are explained on the page.
  const roles = page.getByRole("region", { name: "What each role may do" });
  for (const role of ["Tenant owner", "Bid manager", "Writer / SME", "Reviewer", "Viewer", "Platform admin"]) {
    await expect(roles).toContainText(role);
  }
});

test("integrations: a stored secret only ever says configured, and a new one is written", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings/integrations");
  await expect(page.getByTestId("integration-slack")).toBeVisible();
  await expect(page.getByTestId("secret-status-slack")).toHaveText("Configured (encrypted here)");
  await expect(page.getByLabel("Incoming webhook URL", { exact: true }).first()).toHaveAttribute(
    "placeholder",
    "Stored — type to replace",
  );

  const teams = page.getByTestId("integration-teams");
  await expect(page.getByTestId("secret-status-teams")).toHaveText("Not configured");
  await teams.getByLabel("Send notifications through Microsoft Teams").check();
  await teams.getByLabel("Incoming webhook URL").fill("http://outlook.office.com/webhook/abc");
  await expect(teams.getByText("The webhook URL must be https.")).toBeVisible();
  await expect(teams.getByRole("button", { name: "Save" })).toBeDisabled();
  await teams.getByLabel("Incoming webhook URL").fill("https://outlook.office.com/webhook/abc");
  await teams.getByLabel("Channel name").fill("Bids");
  await teams.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Microsoft Teams saved")).toBeVisible();

  const put = api.requests.find((r) => r.method === "PUT" && r.path === "/api/v1/integrations/teams");
  expect(put?.body).toEqual({
    enabled: true,
    config: { channel: "Bids" },
    webhook_url: "https://outlook.office.com/webhook/abc",
  });
  await expect(page.getByTestId("secret-status-teams")).toHaveText("Configured (encrypted here)");

  // WhatsApp: provider and templates are config; the BSP credential is a reference.
  const whatsapp = page.getByTestId("integration-whatsapp");
  await whatsapp.getByLabel("Provider (BSP)").selectOption("gupshup");
  await whatsapp.getByLabel("Template — High-fit match").fill("bidradar_high_fit");
  await whatsapp.getByLabel("Secret reference (optional)").fill("env:GUPSHUP_API_KEY");
  await whatsapp.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("WhatsApp (India) saved")).toBeVisible();
  const whatsappPut = api.requests.find((r) => r.method === "PUT" && r.path === "/api/v1/integrations/whatsapp");
  expect(whatsappPut?.body).toMatchObject({
    config: { provider: "gupshup", template_high_fit: "bidradar_high_fit" },
    secret_ref: "env:GUPSHUP_API_KEY",
  });
});

test("billing: usage against the plan, the comparison table and a Stripe checkout redirect", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);
  // The provider's hosted page is outside the app; stub it so the redirect is observable.
  await page.route("https://checkout.stripe.test/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Stripe checkout</h1>" }),
  );

  await page.goto("/app/settings/billing");
  await expect(page.getByTestId("current-plan")).toHaveText("Free");
  await expect(page.getByTestId("usage-profiles")).toContainText("1 of 1");
  await expect(page.getByTestId("usage-profiles")).toContainText("At the plan limit");
  await expect(page.getByTestId("usage-agent_drafts_per_month")).toContainText("Not included on this plan");
  await expect(page.getByTestId("plan-comparison")).toContainText("Digest only");
  await expect(page.getByTestId("gst-fields")).toHaveCount(0);

  await page.getByTestId("checkout-pro").click();
  await expect(page).toHaveURL(/checkout\.stripe\.test/);
  const checkout = api.requests.find((r) => r.method === "POST" && r.path === "/api/v1/billing/checkout");
  expect(checkout?.body).toMatchObject({ plan: "pro" });
  expect(String((checkout?.body as { success_url: string }).success_url)).toContain("/app/settings/billing?checkout=success");
});

test("billing: an Indian tenant checks out through Razorpay with GST details", async ({ page, context, baseURL }) => {
  const api = new MockApi({ billingProvider: "razorpay" });
  await api.install(page);
  await signInAs(context, baseURL!);
  await page.route("https://checkout.razorpay.test/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Razorpay checkout</h1>" }),
  );

  await page.goto("/app/settings/billing");
  await expect(page.getByTestId("gst-fields")).toBeVisible();
  await page.getByLabel("GSTIN").fill("29ABCDE1234F1Z5");
  await expect(page.getByText("Place of supply is needed for a GST invoice.")).toBeVisible();
  await expect(page.getByTestId("checkout-pro")).toBeDisabled();
  await page.getByLabel("Place of supply").fill("29");
  await page.getByLabel("Registered name").fill("Example Private Limited");
  await page.getByTestId("checkout-pro").click();
  await expect(page).toHaveURL(/checkout\.razorpay\.test/);
  const checkout = api.requests.find((r) => r.method === "POST" && r.path === "/api/v1/billing/checkout");
  expect(checkout?.body).toMatchObject({
    plan: "pro",
    gst: { gstin: "29ABCDE1234F1Z5", place_of_supply: "29", legal_name: "Example Private Limited" },
  });
});

test("data and privacy: consent, a data request with its SLA, and a confirmed tenant export", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings/privacy");
  await expect(page.getByTestId("consent-terms")).toContainText("Accepted");
  await expect(page.getByTestId("consent-dpdp")).toHaveText("Not accepted");
  await page.getByRole("button", { name: "Accept v2026-01" }).first().click();
  await expect(page.getByTestId("consent-dpdp")).toContainText("Accepted");
  expect(api.consents.some((row) => row.kind === "dpdp")).toBe(true);

  // A personal request: access is answered inline, with an SLA date on the row.
  await page.getByLabel("Request").selectOption("erasure");
  await page.getByLabel("Note (optional)").fill("Please remove my phone number");
  await page.getByRole("button", { name: "Raise request" }).click();
  await expect(page.getByTestId("data-request-row")).toHaveCount(1);
  const row = page.getByTestId("data-request-row").first();
  await expect(row).toContainText("Erasure");
  await expect(row).toContainText("received");
  expect(api.dataRequests[0]).toMatchObject({ kind: "erasure", details: { note: "Please remove my phone number" } });

  // The grievance officer and the sub-processors come from GET /privacy.
  await expect(page.getByTestId("grievance-officer")).toContainText("grievance@bidradar.test");
  await expect(page.getByTestId("sub-processors")).toContainText("Anthropic");

  // Tenant-wide export: owner only, and it takes a typed confirmation.
  await page.getByRole("button", { name: "Export everything" }).click();
  const dialog = page.getByTestId("tenant-confirm-dialog");
  await expect(dialog.getByRole("button", { name: "Start export" })).toBeDisabled();
  await dialog.getByLabel("Type EXPORT to confirm").fill("EXPORT");
  await dialog.getByRole("button", { name: "Start export" }).click();
  await expect(page.getByText(/Export started/)).toBeVisible();
  expect(api.requests.some((r) => r.method === "POST" && r.path === "/api/v1/tenant/export")).toBe(true);
  await expect(page.getByTestId("data-request-row").first()).toContainText("Tenant export");
});

test("data and privacy: a writer sees their own requests but not tenant erasure", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!, { role: "writer" });

  await page.goto("/app/settings/privacy");
  await expect(page.getByTestId("data-requests")).toBeVisible();
  await expect(page.getByTestId("tenant-data-card")).toHaveCount(0);
});
