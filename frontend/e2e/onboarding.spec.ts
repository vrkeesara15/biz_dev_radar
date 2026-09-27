import { expect, test, type Page } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi, baseProfile } from "./mock-api";

test.describe.configure({ mode: "serial" });

async function addPastPerformance(page: Page, index: number, title: string) {
  await page.getByRole("button", { name: "Add record" }).click();
  await page.getByLabel("Title", { exact: true }).nth(index).fill(title);
  await page.getByLabel("Customer", { exact: true }).nth(index).fill("Department of Example");
  await page.getByLabel("Scope", { exact: true }).nth(index).fill("Service desk and application support.");
}

test("US company completes the 7-step wizard against a mocked API", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  // No profile yet -> region choice first.
  await page.goto("/app/onboarding");
  await expect(page.getByRole("heading", { level: 1, name: "Where do you bid?" })).toBeVisible();
  await page.getByLabel("United States").check();
  await page.getByRole("button", { name: "Continue" }).click();

  // Step 1 – identity & registrations (US fields only).
  await expect(page.getByText("Step 1 of 7: Identity & registrations")).toBeVisible();
  await expect(page.getByLabel("UEI (SAM Unique Entity ID)")).toBeVisible();
  await expect(page.getByLabel("PAN", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("GSTIN")).toHaveCount(0);

  await page.getByLabel("Legal name").fill("Acme Federal Services LLC");
  await page.getByLabel("Website", { exact: true }).fill("https://www.acme-federal.example");
  await page.getByLabel("Main phone").fill("+1 703 555 0100");
  await page.getByLabel("Bid-inbox email").fill("bids@acme-federal.example");
  await page.getByLabel("Year founded").fill("2012");
  await page.getByLabel("Legal structure").selectOption("llc");
  await page.getByRole("button", { name: "Add address" }).click();
  await page.getByLabel("Address line 1").fill("100 Main St");
  await page.getByLabel("City").fill("Arlington");
  await page.getByLabel("State", { exact: true }).fill("VA");
  await page.getByLabel("ZIP code").fill("22201");
  await page.getByLabel("UEI (SAM Unique Entity ID)").fill("ABC123DEF456");
  await page.getByLabel("SAM registration status").selectOption("active");
  await page.getByLabel("SAM expiry date").fill("2027-06-30");

  // Autofill: creates the draft profile, shows grouped suggestions with badges.
  const panel = page.getByTestId("autofill-panel");
  await panel.getByRole("button", { name: "Get suggestions" }).click();
  await expect(page.getByTestId("autofill-group-1")).toBeVisible();
  await expect(page.getByTestId("autofill-group-3")).toBeVisible();
  await expect(page.getByTestId("autofill-group-5")).toBeVisible();
  // The IN-only "pan" suggestion is never shown for a US profile.
  await expect(page.getByTestId("autofill-suggestion")).toHaveCount(5);
  await expect(panel.getByText("1 suggestion for the other region hidden.")).toBeVisible();
  await expect(panel.getByText("SAM.gov (UEI)").first()).toBeVisible();
  await expect(panel.getByText("97%")).toBeVisible();

  // Reject one, accept one, then accept all high-confidence.
  await panel.getByRole("button", { name: "Reject Past performance" }).click();
  await panel.getByRole("button", { name: "Accept CAGE code" }).click();
  await expect(page.getByRole("textbox", { name: "CAGE code" })).toHaveValue("7XYZ1");
  await panel.getByRole("button", { name: /Accept all high-confidence/ }).click();
  await expect(page.getByTestId("autofill-suggestion").filter({ has: page.getByText("Applied") })).toHaveCount(3);
  expect(api.collections.codes).toHaveLength(1);
  expect(api.profile?.dba_names).toEqual(["Acme Federal"]);

  // Completeness meter appears once the draft profile exists.
  await expect(page.getByTestId("completeness-meter")).toBeVisible();

  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 2 – meter updated from GET /profiles/{id} (the mock recomputes the score on every read).
  await expect(page.getByText("Step 2 of 7: Size & status")).toBeVisible();
  await expect(page.getByTestId("completeness-score")).toHaveText(api.score());
  expect(Number(api.score())).toBeLessThan(40);
  await expect(page.getByTestId("badge-matching")).toHaveAttribute("data-tone", "off");
  await expect(page.getByLabel("Socio-economic certifications")).toBeVisible();
  await expect(page.getByLabel("Net worth")).toHaveCount(0);
  await page.getByLabel("Employees (total)").fill("48");
  await page.getByRole("button", { name: "Add fiscal year" }).click();
  await page.getByLabel("Revenue", { exact: true }).fill("12500000");
  await expect(page.getByText("$12,500,000.00")).toBeVisible();
  await page.getByLabel("Bonding capacity / bank guarantee limit", { exact: true }).fill("5000000");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 3 – matching switches on at 40.
  await expect(page.getByText("Step 3 of 7: What we sell")).toBeVisible();
  await expect(page.getByTestId("completeness-score")).toHaveText(api.score());
  expect(Number(api.score())).toBeGreaterThanOrEqual(40);
  await expect(page.getByTestId("badge-matching")).toHaveAttribute("data-tone", "on");
  await expect(page.getByLabel("Scheme").first()).toContainText("NAICS");
  await expect(page.getByLabel("Scheme").first()).not.toContainText("GeM");
  await page.getByRole("button", { name: "Add code" }).click();
  await page.getByLabel("Code", { exact: true }).nth(1).fill("541511");
  await page.getByRole("button", { name: "Add keyword" }).click();
  await page.getByLabel("Term").fill("cloud migration");
  await page.getByLabel("Weight").fill("2");
  await page.getByRole("button", { name: "Add service line" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Cloud engineering");
  await page.getByLabel("Description").fill("We migrate and operate workloads on AWS and Azure for federal agencies.");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 4.
  await expect(page.getByText("Step 4 of 7: Where & how big")).toBeVisible();
  await expect(page.getByTestId("completeness-score")).toHaveText(api.score());
  await expect(page.getByLabel("Target US states")).toBeVisible();
  await expect(page.getByLabel("Target Indian states / UTs")).toHaveCount(0);
  await page.getByLabel("Target US states").fill("VA, MD, DC");
  await page.getByLabel("Minimum value (USD)", { exact: true }).fill("250000");
  await page.getByLabel("Maximum value (USD)", { exact: true }).fill("25000000");
  await page.getByLabel("RFP", { exact: true }).check();
  await page.getByLabel("Target agencies / ministries / PSUs").fill("GSA");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 5 – three past performances unlock drafting.
  await expect(page.getByText("Step 5 of 7: Proof")).toBeVisible();
  await expect(page.getByTestId("completeness-score")).toHaveText(api.score());
  // Score is already >= 70 here, yet drafting stays off without 3 past performances.
  expect(Number(api.score())).toBeGreaterThanOrEqual(70);
  await expect(page.getByTestId("badge-drafting")).toHaveAttribute("data-tone", "off");
  await expect(page.getByText("0 of 3 recommended")).toBeVisible();
  await addPastPerformance(page, 0, "Service desk modernization");
  await addPastPerformance(page, 1, "Cloud migration for a bureau");
  await addPastPerformance(page, 2, "Data platform operations");
  await expect(page.getByText("3 of 3 recommended")).toBeVisible();
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 6.
  await expect(page.getByText("Step 6 of 7: Preferences")).toBeVisible();
  await expect(page.getByTestId("completeness-score")).toHaveText(api.score());
  await expect(page.getByTestId("badge-drafting")).toHaveAttribute("data-tone", "on");
  await expect(page.getByLabel("High-fit match via Email")).toBeChecked();
  await expect(page.getByLabel("Hindi summaries")).toHaveCount(0);
  await page.getByLabel("High-fit match via Slack").check();
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 7 – review.
  await expect(page.getByText("Step 7 of 7: Review")).toBeVisible();
  await expect(page.getByTestId("completeness-score").first()).toHaveText(api.score());
  await expect(page.getByTestId("badge-matching").first()).toHaveText("Matching on");
  await expect(page.getByTestId("badge-drafting").first()).toHaveText("Drafting on");
  await expect(page.getByTestId("count-past-performance")).toHaveText("Past performance: 3");
  await expect(page.getByTestId("count-codes")).toHaveText("Codes: 2");
  await expect(page.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute("href", "/app");

  // The wizard only ever wrote through the normal endpoints.
  expect(api.requests.filter((r) => r.path.endsWith("/autofill"))).toHaveLength(1);
  expect(api.prefs.channels_by_event).toMatchObject({ high_fit_match: ["email", "slack"] });
  expect(api.profile).toMatchObject({ uei: "ABC123DEF456", cage_code: "7XYZ1", target_us_states: ["VA", "MD", "DC"] });
  expect(api.profile).not.toHaveProperty("pan", "ABCDE1234F");
});

test("resumes an existing profile at the requested step and hides US fields for India", async ({ page, context, baseURL }) => {
  const api = new MockApi({ region: "in", autofill: false });
  await api.install(page);
  await signInAs(context, baseURL!);
  // Seed an existing IN profile so the wizard resumes instead of asking for a region.
  api.profile = {
    ...baseProfile(),
    id: "33333333-3333-3333-3333-333333333333",
    region: "in",
    legal_name: "Bharat Systems Pvt Ltd",
    pan: "•••••234F",
  };

  await page.goto("/app/onboarding?step=1");
  await expect(page.getByText("Step 1 of 7: Identity & registrations")).toBeVisible();
  await expect(page.getByLabel("Legal name")).toHaveValue("Bharat Systems Pvt Ltd");
  await expect(page.getByLabel("PAN", { exact: true })).toHaveValue("•••••234F");
  await expect(page.getByLabel("GSTIN")).toBeVisible();
  await expect(page.getByLabel("UEI (SAM Unique Entity ID)")).toHaveCount(0);
  await expect(page.getByLabel("CAGE code")).toHaveCount(0);
  await expect(page.getByLabel("Portal enrolments and DSC")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Autofill from your website, capability statement" })).toBeVisible();

  // Autofill endpoint absent -> "not available" notice, nothing else breaks.
  await page.getByLabel("Website URL").fill("https://bharat.example");
  await page.getByRole("button", { name: "Get suggestions" }).click();
  await expect(page.getByTestId("autofill-notice")).toContainText("not available");

  // Stepper navigation works because a profile exists.
  await page.getByRole("button", { name: "Step 4: Where & how big" }).click();
  await expect(page.getByText("Step 4 of 7: Where & how big")).toBeVisible();
  await expect(page.getByLabel("Target Indian states / UTs")).toBeVisible();
  await expect(page.getByLabel("Target US states")).toHaveCount(0);
  await expect(page.getByLabel("GeM bid")).toBeVisible();
  await expect(page.getByLabel("Sources sought")).toHaveCount(0);
  await expect(page.getByLabel("Minimum value (INR)", { exact: true })).toBeVisible();
  await page.getByLabel("Minimum value (INR)", { exact: true }).fill("1500000");
  await expect(page.getByText("₹15,00,000.00")).toBeVisible();
});
