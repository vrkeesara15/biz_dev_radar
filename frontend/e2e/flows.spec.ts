/**
 * M7-13 — the five SPEC 12 UI flows, chained through the real screens against
 * the mock API, with an axe-core check on every page each flow visits.
 *
 *   onboarding  sign-in -> region -> the 7-step wizard past completeness 70
 *   search      home -> /app/opportunities with filters -> one notice
 *   pursue      Pursue on the notice -> the board -> the workspace -> calendar
 *   review      Gate 1 -> the draft with its citations -> approve a section
 *   export      Gate 2 -> mark final -> an export URL comes back
 *
 * The five run in `serial` mode on ONE page and ONE MockApi, so what a later
 * flow reads is what an earlier flow actually wrote: the pursuit the Pursue
 * click returned is the pursuit the workspace opens, and the package the
 * export carries is the one Gate 2 approved. A failure therefore also tells
 * you which step of the chain broke.
 *
 * The settings tabs and the admin console are not part of a SPEC flow but are
 * screens a user reaches from the same nav, so they get the same axe sweep at
 * the end (the admin console needs the platform-admin session, which is why it
 * is last).
 */
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { expectNoA11yViolations } from "./axe";
import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

test.describe.configure({ mode: "serial" });

/** The fixtures' VA cloud-migration notice and the pursuit that hangs off it. */
const CLOUD_ID = "7c1d2e3f-0000-4000-8000-000000000001";
const CLOUD_PURSUIT = "9a000000-0000-4000-8000-000000000001";

let browserContext: BrowserContext;
let page: Page;
let api: MockApi;
let base: string;

async function openContext(browser: Browser, baseURL: string) {
  // An Indian reader on a US buyer's clock: the dual-time-zone rendering SPEC 9
  // asks for is on screen for every axe check, not just the US-only case.
  browserContext = await browser.newContext({ timezoneId: "Asia/Kolkata", locale: "en-US" });
  page = await browserContext.newPage();
  api = new MockApi({
    region: "us",
    autofill: true,
    dashboard: true,
    matches: true,
    pursuits: true,
    workspace: true,
    members: true,
    pipelineActions: true,
    savedSearches: true,
  });
  await api.install(page);
  await signInAs(browserContext, baseURL);
  base = baseURL;
}

test.beforeAll(async ({ browser, baseURL }) => {
  await openContext(browser, baseURL!);
});

test.afterAll(async () => {
  await browserContext?.close();
});

// --- flow 1: onboarding ---------------------------------------------------------

test("flow 1 — onboarding: sign-in, region, and the wizard past completeness 70", async ({
  browser,
}) => {
  test.setTimeout(420_000);

  // The sign-in screen itself, with no session at all.
  const anonymous = await browser.newContext();
  const anonymousPage = await anonymous.newPage();
  await anonymousPage.goto(`${base}/signin`);
  await expect(
    anonymousPage.getByRole("heading", { level: 1, name: "Sign in to BidRadar" }),
  ).toBeVisible();
  await expectNoA11yViolations(anonymousPage, "sign-in");
  await anonymous.close();

  // Onboarding is the one flow that needs a tenant with NO company profile, so
  // it runs on its own context and its own mock: the shared one is seeded with
  // the workspace fixture's finished profile, which would skip the wizard.
  const context = await browser.newContext({ timezoneId: "Asia/Kolkata", locale: "en-US" });
  const wizard = await context.newPage();
  const wizardApi = new MockApi({ region: "us", autofill: true });
  await wizardApi.install(wizard);
  await signInAs(context, base);
  const page = wizard;
  const api = wizardApi;

  // No profile yet, so the wizard asks for a region first.
  await page.goto("/app/onboarding");
  await expect(page.getByRole("heading", { level: 1, name: "Where do you bid?" })).toBeVisible();
  await expectNoA11yViolations(page, "onboarding / region");
  await page.getByLabel("United States").check();
  await page.getByRole("button", { name: "Continue" }).click();

  // Step 1 — identity and registrations.
  await expect(page.getByText("Step 1 of 7: Identity & registrations")).toBeVisible();
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
  // The autofill panel is part of step 1: run it so the suggestions, their
  // confidence badges and the accept/reject controls are on screen for axe,
  // and accept the high-confidence ones the way a real user would (SPEC 4 —
  // nothing is stored until a human accepts it).
  const autofill = page.getByTestId("autofill-panel");
  await autofill.getByRole("button", { name: "Get suggestions" }).click();
  await expect(page.getByTestId("autofill-group-1")).toBeVisible();
  await expectNoA11yViolations(page, "wizard step 1 (identity, with autofill suggestions)");
  await autofill.getByRole("button", { name: /Accept all high-confidence/ }).click();
  await expect(page.getByRole("textbox", { name: "CAGE code" })).toHaveValue("7XYZ1");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 2 — size and status.
  await expect(page.getByText("Step 2 of 7: Size & status")).toBeVisible();
  await page.getByLabel("Employees (total)").fill("48");
  await page.getByRole("button", { name: "Add fiscal year" }).click();
  await page.getByLabel("Revenue", { exact: true }).fill("12500000");
  await page.getByLabel("Bonding capacity / bank guarantee limit", { exact: true }).fill("5000000");
  await expectNoA11yViolations(page, "wizard step 2 (size & status)");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 3 — what we sell.
  await expect(page.getByText("Step 3 of 7: What we sell")).toBeVisible();
  await page.getByRole("button", { name: "Add code" }).click();
  // `.last()`: this flow accepts no autofill suggestion, so "Add code" makes the
  // first row rather than a second one.
  await page.getByLabel("Code", { exact: true }).last().fill("541511");
  await page.getByRole("button", { name: "Add keyword" }).click();
  await page.getByLabel("Term").fill("cloud migration");
  await page.getByLabel("Weight").fill("2");
  await page.getByRole("button", { name: "Add service line" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Cloud engineering");
  await page
    .getByLabel("Description")
    .fill("We migrate and operate workloads on AWS and Azure for federal agencies.");
  await expectNoA11yViolations(page, "wizard step 3 (what we sell)");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 4 — where and how big.
  await expect(page.getByText("Step 4 of 7: Where & how big")).toBeVisible();
  await page.getByLabel("Target US states").fill("VA, MD, DC");
  await page.getByLabel("Minimum value (USD)", { exact: true }).fill("250000");
  await page.getByLabel("Maximum value (USD)", { exact: true }).fill("25000000");
  await page.getByLabel("RFP", { exact: true }).check();
  await page.getByLabel("Target agencies / ministries / PSUs").fill("GSA");
  await expectNoA11yViolations(page, "wizard step 4 (where & how big)");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 5 — proof. SPEC 4: the score passes 70 here, and three past
  // performances are what actually unlock drafting.
  await expect(page.getByText("Step 5 of 7: Proof")).toBeVisible();
  expect(Number(api.score())).toBeGreaterThanOrEqual(70);
  for (const [index, title] of [
    "Service desk modernization",
    "Cloud migration for a bureau",
    "Data platform operations",
  ].entries()) {
    await page.getByRole("button", { name: "Add record" }).click();
    await page.getByLabel("Title", { exact: true }).nth(index).fill(title);
    await page.getByLabel("Customer", { exact: true }).nth(index).fill("Department of Example");
    await page
      .getByLabel("Scope", { exact: true })
      .nth(index)
      .fill("Service desk and application support.");
  }
  await expect(page.getByText("3 of 3 recommended")).toBeVisible();
  await expectNoA11yViolations(page, "wizard step 5 (proof)");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 6 — preferences.
  await expect(page.getByText("Step 6 of 7: Preferences")).toBeVisible();
  await expect(page.getByTestId("badge-drafting")).toHaveAttribute("data-tone", "on");
  await expectNoA11yViolations(page, "wizard step 6 (preferences)");
  await page.getByRole("button", { name: "Save and continue" }).click();

  // Step 7 — review: completeness is at or over 70 and matching is on.
  await expect(page.getByText("Step 7 of 7: Review")).toBeVisible();
  await expect(page.getByTestId("completeness-score").first()).toHaveText(api.score());
  expect(Number(api.score())).toBeGreaterThanOrEqual(70);
  await expect(page.getByTestId("badge-matching").first()).toHaveText("Matching on");
  await expectNoA11yViolations(page, "wizard step 7 (review)");

  await context.close();
});

// --- flow 2: search --------------------------------------------------------------

test("flow 2 — search: home, filtered search, and one notice opened", async () => {
  test.setTimeout(120_000);

  // Home is where the flow starts for a returning user.
  await page.goto("/app");
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
  await expect(page.getByTestId("high-fit-card")).toContainText("Cloud migration");
  await expectNoA11yViolations(page, "home");

  // Search, unfiltered.
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Opportunities" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Opportunities" })).toBeVisible();
  await expect(page.getByTestId("result-row")).toHaveCount(3);
  await expectNoA11yViolations(page, "opportunities search (unfiltered)");

  // Filters: region, notice type and a minimum fit score, all URL-synced and
  // all sent to the API (SPEC 10.4 screen 3).
  // Each control pushes the URL and re-renders the bar, so wait for the URL to
  // settle before touching the next one (this is the app's own round trip, not
  // an arbitrary sleep).
  await page.getByLabel("Region", { exact: true }).selectOption("us");
  await expect(page).toHaveURL(/region=us/);
  await page.getByLabel("RFP", { exact: true }).check();
  await expect(page).toHaveURL(/type=rfp/);
  await page.getByLabel("Search", { exact: true }).fill("cloud");
  await page.getByLabel("Search", { exact: true }).press("Enter");
  await expect(page).toHaveURL(/q=cloud/);
  await expect(page.getByTestId("result-row")).toHaveCount(1);
  const query = api.requests.filter((row) => row.path === "/api/v1/opportunities").at(-1);
  expect(query?.search).toContain("q=cloud");
  expect(query?.search).toContain("region=us");
  await expectNoA11yViolations(page, "opportunities search (filtered)");

  // Open the notice.
  await page.getByRole("link", { name: /Cloud migration and managed services/ }).click();
  await expect(page).toHaveURL(new RegExp(`/app/opportunities/${CLOUD_ID}$`));
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Cloud migration");
  await expect(page.getByTestId("documents-card").getByTestId("document-row")).toHaveCount(2);
  await expect(page.getByTestId("disclaimer").first()).toBeVisible();
  await expectNoA11yViolations(page, "opportunity detail");
});

// --- flow 3: pursue --------------------------------------------------------------

test("flow 3 — pursue: the notice goes on the board and opens as a workspace", async () => {
  test.setTimeout(120_000);

  await page.goto(`/app/opportunities/${CLOUD_ID}`);
  await page.getByTestId("action-bar").getByRole("button", { name: "Pursue" }).click();
  await expect(page.getByText("Added to your pipeline")).toBeVisible();
  const pursued = api.requests.find(
    (row) => row.method === "POST" && row.path === `/api/v1/opportunities/${CLOUD_ID}/pursue`,
  );
  expect(pursued).toBeTruthy();

  // The board: one column per SPEC 9 stage, the pursued notice as a card.
  await page.goto("/app/pipeline");
  await expect(page.getByRole("heading", { level: 1, name: "Pipeline" })).toBeVisible();
  await expect(page.getByTestId("board").getByTestId("board-column")).toHaveCount(7);
  const card = page.locator(`[data-pursuit-id="${CLOUD_PURSUIT}"]`);
  await expect(card).toContainText("Cloud migration and managed services");
  await expectNoA11yViolations(page, "pipeline board");

  // The same filter set drives the table view (SPEC 9 offers both).
  await page.getByRole("button", { name: "Table" }).click();
  await expect(page).toHaveURL(/view=table/);
  await expect(page.getByRole("table")).toBeVisible();
  await expectNoA11yViolations(page, "pipeline table");

  // The calendar is the third view of the same key dates (SPEC 10.4 screen 7).
  await page.goto("/app/calendar?date=2026-11-01");
  await expect(page.getByRole("heading", { level: 1, name: "Calendar" })).toBeVisible();
  await expect(page.getByTestId("calendar-title")).toHaveText("November 2026");
  await expectNoA11yViolations(page, "calendar");

  // Into the workspace the Pursue click put on the board.
  await page.goto("/app/pipeline");
  await page.locator(`[data-pursuit-id="${CLOUD_PURSUIT}"]`).getByRole("link").first().click();
  await expect(page).toHaveURL(new RegExp(`/app/pursuits/${CLOUD_PURSUIT}`));
  await expect(page.getByTestId("workspace-title")).toContainText("Cloud migration");
  await expect(page.getByRole("tab")).toHaveCount(7);
});

// --- flow 4: review --------------------------------------------------------------

test("flow 4 — review: Gate 1, the draft with its citations, and a section approved", async () => {
  test.setTimeout(180_000);

  await page.goto(`/app/pursuits/${CLOUD_PURSUIT}`);

  // The bid/no-bid tab now has a real scorecard: the artifacts route serves it
  // (M7-14 closed OQ-147), so the panel is data, not an empty state.
  const scorecard = page.getByTestId("scorecard");
  await expect(scorecard.getByTestId("scorecard-criterion")).toHaveCount(6);
  await expect(scorecard.getByTestId("scorecard-recommendation")).toContainText("bid");
  await expectNoA11yViolations(page, "workspace / bid-no-bid tab");

  // Gate 1: a note and the decision, which moves the pursuit into drafting.
  await page.getByTestId("gate-1").getByLabel("Note").fill("Fit is strong and the window is long enough.");
  await page.getByTestId("decide-bid").click();
  await expect(page.getByTestId("decision-record")).toContainText("bid by E2E Owner");
  await expect(page.getByTestId("pursuit-stage")).toHaveText("Drafting");

  // The compliance matrix, with a citation that points at the document page.
  await page.getByRole("tab", { name: /Compliance matrix/ }).click();
  await expect(page.getByTestId("matrix-row")).toHaveCount(2);
  await expect(page.getByTestId("matrix-row").first().getByTestId("matrix-citation")).toHaveText("page 12");
  await expectNoA11yViolations(page, "workspace / compliance matrix tab");

  // The drafts tab: the section loads with its citations panel and its flags.
  await page.getByRole("tab", { name: /Drafts/ }).click();
  await expect(page.getByTestId("section-row")).toHaveCount(2);
  const citations = page.getByTestId("citations-panel");
  await expect(citations.getByTestId("citation-row")).toHaveCount(1);
  await expect(citations).toContainText("page 3");
  await expect(page.locator("[data-unsupported]")).toHaveCount(1);
  await expect(page.getByTestId("ai-disclaimer")).toBeVisible();
  await expectNoA11yViolations(page, "workspace / drafts tab");

  // Approve the section a reviewer has read.
  await page.getByTestId("approve-section").click();
  await expect(page.getByTestId("approve-section")).toContainText("Approved");
  const approved = api.requests.find(
    (row) => row.method === "POST" && /\/drafts\/[^/]+\/approve$/.test(row.path),
  );
  expect(approved).toBeTruthy();

  // The remaining tabs a reviewer walks, each with its own axe check.
  await page.getByRole("tab", { name: /Pricing/ }).click();
  await expect(page.getByTestId("pricing-summary")).toBeVisible();
  await expectNoA11yViolations(page, "workspace / pricing tab");

  await page.getByRole("tab", { name: /Checklist/ }).click();
  await expect(page.getByTestId("checklist-item")).toHaveCount(3);
  await expectNoA11yViolations(page, "workspace / checklist tab");

  await page.getByRole("tab", { name: /Tasks/ }).click();
  await expect(page.getByTestId("tasks-panel")).toBeVisible();
  await expectNoA11yViolations(page, "workspace / tasks tab");

  await page.getByRole("tab", { name: /Activity/ }).click();
  await expect(page.getByTestId("panel-activity")).toBeVisible();
  await expectNoA11yViolations(page, "workspace / activity tab");
});

// --- flow 5: export --------------------------------------------------------------

test("flow 5 — export: Gate 2, mark final, and a signed URL for the package", async () => {
  test.setTimeout(120_000);

  await page.goto(`/app/pursuits/${CLOUD_PURSUIT}`);

  // Gate 2 refuses until the red-team reviewer has run — the server's reason,
  // shown as the server words it.
  await page.getByTestId("approve-package").click();
  await expect(page.getByTestId("gate-2-error")).toContainText("the red-team reviewer has not run yet");
  await expect(page.getByTestId("mark-final")).toBeDisabled();
  await expectNoA11yViolations(page, "workspace / Gate 2 refused");

  await page.getByTestId("agent-step").selectOption("red_team");
  await page.getByTestId("run-step").click();
  await expect.poll(() => api.redTeamRan).toBe(true);

  await page.getByTestId("approve-package").click();
  await expect(page.getByTestId("package-approved")).toBeVisible();

  // Marking final is what drops the "DRAFT — internal" footer (SPEC 11).
  await page.getByTestId("mark-final").click();
  await expect(page.getByTestId("package-final")).toBeVisible();

  // The export returns a URL on the row.
  const popup = browserContext.waitForEvent("page").catch(() => null);
  await page.getByTestId("export-docx").click();
  const row = page.getByTestId("export-row").first();
  await expect(row).toContainText("36C24825R0042_package.docx");
  await expect(row).toContainText("final");
  await expect(row.getByRole("link")).toHaveAttribute(
    "href",
    "https://files.bidradar.test/exports/docx?signature=e2e",
  );
  await popup;
  await expectNoA11yViolations(page, "workspace / export complete");
});

// --- the rest of the nav: settings and the admin console --------------------------

test("settings tabs and the admin console pass the same axe check", async () => {
  test.setTimeout(180_000);

  const tabs: [string, string][] = [
    ["/app/settings/profile", "settings / profile"],
    ["/app/settings/users", "settings / users & roles"],
    ["/app/settings/notifications", "settings / notifications"],
    ["/app/settings/saved-searches", "settings / saved searches"],
    ["/app/settings/integrations", "settings / integrations"],
    ["/app/settings/billing", "settings / billing"],
    ["/app/settings/privacy", "settings / data & privacy"],
  ];
  for (const [path, label] of tabs) {
    await page.goto(path);
    await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
    await expectNoA11yViolations(page, label);
  }

  // The console is a platform-admin screen: swap the session, not the app.
  await signInAs(browserContext, base, { role: "platform_admin" });
  await page.goto("/app/admin");
  await expect(page.getByRole("heading", { level: 1, name: "Admin console" })).toBeVisible();
  await expectNoA11yViolations(page, "admin console");
});
