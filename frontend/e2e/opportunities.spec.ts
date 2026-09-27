import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

const DISCLAIMER = "Verify every detail on the official portal before submitting.";
const CLOUD_ID = "7c1d2e3f-0000-4000-8000-000000000001";

// An Indian reader looking at a US buyer: the SPEC 9 example "2:00 PM EDT = 11:30 PM IST".
test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("search renders rows from the mocked API with dual time zones, values and sources", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/opportunities");
  await expect(page.getByRole("heading", { level: 1, name: "Opportunities" })).toBeVisible();
  const rows = page.getByTestId("result-row");
  await expect(rows).toHaveCount(3);
  await expect(page.getByText("3 notices.")).toBeVisible();

  // Column headers as in SPEC 10.4 screen 3.
  const table = page.getByTestId("results-table");
  for (const name of ["Score", "Title", "Buyer", "Value", "Due", "Type", "Source"]) {
    await expect(table.getByRole("columnheader", { name })).toBeVisible();
  }

  // Row 1: US RFP with a USD range, buyer hierarchy, EDT deadline rendered with IST alongside.
  const cloud = rows.filter({ hasText: "Cloud migration" });
  await expect(cloud.getByRole("link", { name: "Cloud migration and managed services for VA regional offices" })).toHaveAttribute(
    "href",
    `/app/opportunities/${CLOUD_ID}`,
  );
  await expect(cloud).toContainText("Department of Veterans Affairs");
  await expect(cloud).toContainText("$2.50M – $4.00M");
  await expect(cloud.locator("time")).toHaveText("Oct 14, 2:00 PM EDT=11:30 PM IST");
  await expect(cloud.locator("[data-tone]")).toBeVisible();
  await expect(cloud.getByLabel("Not scored yet")).toHaveText("—");
  await expect(cloud.getByRole("link", { name: /SAM\.gov/ })).toHaveAttribute("href", "https://sam.gov/opp/36C24825R0042/view");

  // Row 2: Indian GeM bid in lakh/crore with the USD normalisation; IST buyer = IST reader collapses to one clock.
  const laptops = rows.filter({ hasText: "laptops" });
  await expect(laptops).toContainText("₹1.20 Cr – ₹2.00 Cr");
  await expect(laptops).toContainText("≈ $144.0K – $240.0K");
  await expect(laptops.locator("time")).toHaveText("Oct 20, 3:00 PM IST");
  await expect(laptops.locator("[data-notice-type=gem_bid]")).toHaveText("GeM bid");

  // Row 3: grant without a deadline or value.
  const grant = rows.filter({ hasText: "Community economic development" });
  await expect(grant.getByRole("link", { name: /Grants\.gov/ })).toBeVisible();

  // Disclaimer in the list footer.
  await expect(page.getByTestId("disclaimer")).toHaveText(DISCLAIMER);
});

test("filters are URL-synced and drive the API query; a URL is a saved search", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/opportunities");
  await expect(page.getByTestId("result-row")).toHaveCount(3);

  await page.getByLabel("Region", { exact: true }).selectOption("in");
  await expect(page).toHaveURL(/\/app\/opportunities\?region=in$/);
  await expect(page.getByTestId("result-row")).toHaveCount(1);
  expect(api.requests.some((r) => r.path === "/api/v1/opportunities" && r.search.includes("region=in"))).toBe(true);

  // A notice type that no Indian row has -> empty state with a clear action.
  await page.getByLabel("RFP", { exact: true }).check();
  await expect(page).toHaveURL(/region=in&type=rfp/);
  await expect(page.getByTestId("results-empty")).toBeVisible();
  await expect(page.getByText("No opportunities match")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page).toHaveURL(/\/app\/opportunities$/);
  await expect(page.getByTestId("result-row")).toHaveCount(3);

  // Full-text query commits on Enter.
  await page.getByLabel("Search", { exact: true }).fill("laptops");
  await page.getByLabel("Search", { exact: true }).press("Enter");
  await expect(page).toHaveURL(/\?q=laptops$/);
  await expect(page.getByTestId("result-row")).toHaveCount(1);

  // Landing on a saved URL restores every control.
  await page.goto("/app/opportunities?q=cloud&region=us&type=rfp,rfq&min_score=70&due_before=2026-10-31");
  await expect(page.getByLabel("Search", { exact: true })).toHaveValue("cloud");
  await expect(page.getByLabel("Region", { exact: true })).toHaveValue("us");
  await expect(page.getByLabel("RFP", { exact: true })).toBeChecked();
  await expect(page.getByLabel("RFQ", { exact: true })).toBeChecked();
  await expect(page.getByLabel("Due before")).toHaveValue("2026-10-31");
  await expect(page.getByLabel("Minimum fit score")).toHaveValue("70");
  await expect(page.locator("output[for=filter-min-score]")).toHaveText("≥ 70");
  await expect(page.getByTestId("result-row")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /Clear \(5\)/ })).toBeVisible();

  // Saved-search bar: the API is not there yet (M4-08) and the UI says so instead of faking it.
  const bar = page.getByTestId("saved-search-bar");
  await expect(bar).toContainText("Saved searches arrive with the matching milestone");
  await bar.getByRole("button", { name: "Save this search" }).click();
  const dialog = page.getByRole("dialog", { name: "Save this search" });
  await expect(dialog.getByLabel("Name")).toHaveValue("“cloud” · United States · RFP/RFQ · due by 2026-10-31 · score ≥ 70");
  await expect(dialog.getByLabel("Search link")).toHaveValue(/\/app\/opportunities\?q=cloud&region=us&type=rfp%2Crfq&due_before=2026-10-31&min_score=70$/);
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved searches arrive with the matching milestone").last()).toBeVisible();
  const post = api.requests.find((r) => r.method === "POST" && r.path === "/api/v1/saved-searches");
  expect(post?.body).toMatchObject({ filters: { q: "cloud", region: "us", type: "rfp,rfq", min_score: "70" } });
});

test("saved searches list and apply when the API exists", async ({ page, context, baseURL }) => {
  const api = new MockApi({ savedSearches: true });
  api.savedSearches.push({ id: "saved-1", name: "India GeM bids", filters: { region: "in", type: "gem_bid" } });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/opportunities");
  await page.getByTestId("saved-search-bar").getByRole("button", { name: "India GeM bids" }).click();
  await expect(page).toHaveURL(/\?region=in&type=gem_bid$/);
  await expect(page.getByTestId("result-row")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "India GeM bids" })).toHaveAttribute("aria-pressed", "true");
});

test("detail page shows summary, dual-zone dates, eligibility, documents, versions diff, actions and the disclaimer", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/opportunities/${CLOUD_ID}`);
  await expect(page.getByRole("heading", { level: 1, name: "Cloud migration and managed services for VA regional offices" })).toBeVisible();

  // Buyer hierarchy breadcrumb and badges.
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(crumbs).toContainText("Department of Veterans Affairs");
  await expect(crumbs).toContainText("Veterans Health Administration");
  await expect(crumbs).toContainText("Network Contracting Office 8");
  await expect(page.locator("[data-notice-type=rfp]").first()).toHaveText("RFP");
  await expect(page.locator("[data-status=open]").first()).toHaveText("Open");

  // Buyer's clock first, the reader's second (SPEC 9).
  const header = page.locator("article > header");
  await expect(header.getByText("Response due")).toBeVisible();
  await expect(header.locator("time").first()).toHaveText("Oct 14, 2026, 2:00 PM EDT=11:30 PM IST");
  await expect(header.locator("[data-tone]").first()).toBeVisible();

  // Summary lines and the fit-score placeholder.
  await expect(page.getByTestId("summary-card")).toContainText("Migration of 14 on-premise applications");
  await expect(page.getByTestId("not-scored")).toHaveText("Not scored yet");
  await expect(page.getByTestId("fit-score-card")).toContainText("Semantic similarity");
  await expect(page.getByTestId("fit-score-card")).toContainText("Eligibility risks");

  // Eligibility checks with unknown icons until matched.
  const eligibility = page.getByTestId("eligibility-card");
  await expect(eligibility).toContainText("Minimum experience");
  await expect(eligibility).toContainText("FedRAMP High, ISO 27001");
  await expect(eligibility.getByRole("img", { name: "Unknown" })).toHaveCount(3);

  // Documents with size/pages/status and the source link.
  const docs = page.getByTestId("documents-card");
  await expect(docs.getByTestId("document-row")).toHaveCount(2);
  await expect(docs).toContainText("2.5 MB · 24 pages · application/pdf");
  await expect(docs.locator("[data-doc-status=pending]")).toHaveText("pending");
  await expect(docs.getByRole("link", { name: /RFP-36C24825R0042\.pdf/ })).toHaveAttribute("href", /sam\.gov/);

  // Versions: field diff old -> new with change-kind badges.
  const versions = page.getByTestId("versions-card");
  await expect(versions.getByTestId("version-row")).toHaveCount(2);
  const v2 = versions.locator("[data-version='2']");
  await expect(v2.locator("[data-change-kind=deadline_moved]")).toHaveText("Deadline moved");
  await expect(v2.locator("[data-change-kind=new_attachment]")).toHaveText("New attachment");
  const dueDiff = v2.locator("[data-diff-field=response_due_at]");
  await expect(dueDiff).toContainText("Response due at");
  await expect(dueDiff).toContainText("Oct 7, 2026, 2:00 PM EDT");
  await expect(dueDiff).toContainText("Oct 14, 2026, 2:00 PM EDT");
  await expect(v2.locator("[data-diff-field=documents]")).toContainText("RFP-36C24825R0042.pdf, Amendment-0001.pdf");
  await expect(versions.locator("[data-version='1']")).toContainText("Initial capture");

  // Contacts, also-from links, key facts.
  await expect(page.getByTestId("contacts-card").getByRole("link", { name: "jordan.ellis@example.va.gov" })).toBeVisible();
  await expect(page.getByTestId("also-from-card").getByRole("link", { name: /HG-99123/ })).toHaveAttribute("href", /highergov\.com/);
  await expect(page.getByTestId("facts-card")).toContainText("Bay Pines, FL, US (remote allowed)");
  await expect(page.getByTestId("facts-card")).toContainText("Example Federal Systems LLC");

  // Attribution footer with the official link and the disclaimer.
  const footer = page.getByTestId("attribution-footer");
  await expect(footer.getByRole("link", { name: /SAM\.gov/ })).toHaveAttribute("href", "https://sam.gov/opp/36C24825R0042/view");
  await expect(footer.getByTestId("disclaimer")).toHaveText(DISCLAIMER);

  // Actions POST the M6-01 routes; without them the UI explains instead of pretending.
  const actions = page.getByTestId("action-bar");
  await actions.getByRole("button", { name: "Pursue" }).click();
  await expect(page.getByText("Pipeline actions arrive with the pursuits milestone").first()).toBeVisible();
  expect(api.requests.some((r) => r.method === "POST" && r.path === `/api/v1/opportunities/${CLOUD_ID}/pursue`)).toBe(true);

  await actions.getByRole("button", { name: "Pass" }).click();
  const passDialog = page.getByRole("dialog", { name: "Pass on this opportunity" });
  await expect(passDialog.getByRole("button", { name: "Pass with reason" })).toBeDisabled();
  await passDialog.getByLabel("Reason").fill("Set-aside we cannot meet");
  await passDialog.getByRole("button", { name: "Pass with reason" }).click();
  const pass = api.requests.find((r) => r.method === "POST" && r.path === `/api/v1/opportunities/${CLOUD_ID}/pass`);
  expect(pass?.body).toEqual({ reason: "Set-aside we cannot meet" });
});

test("pipeline actions succeed when the API exists and a missing record shows not-found", async ({ page, context, baseURL }) => {
  const api = new MockApi({ pipelineActions: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/opportunities/${CLOUD_ID}`);
  await page.getByTestId("action-bar").getByRole("button", { name: "Watch" }).click();
  await expect(page.getByText("Watching. You will hear about amendments and deadline moves.")).toBeVisible();

  await page.goto("/app/opportunities/00000000-0000-4000-8000-00000000dead");
  await expect(page.getByTestId("detail-error")).toContainText("Opportunity not found");
  await expect(page.getByRole("link", { name: "Back to search" })).toHaveAttribute("href", "/app/opportunities");
});

test("home links High-fit today to the search with min_score=70 and the nav is live", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app");
  await expect(page.getByRole("link", { name: "High-fit today" })).toHaveAttribute("href", "/app/opportunities?min_score=70");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Opportunities" }).click();
  await expect(page).toHaveURL(/\/app\/opportunities$/);
  await expect(page.getByRole("link", { name: "Opportunities" }).first()).toHaveAttribute("aria-current", "page");
});
