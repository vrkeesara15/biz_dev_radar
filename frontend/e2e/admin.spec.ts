import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi, currentPeriod } from "./mock-api";

const ADMIN = { role: "platform_admin" as const };

test("the console is closed to tenant users and open to platform admins", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/admin");
  await expect(page.getByTestId("admin-forbidden")).toContainText("403");
  // and the nav does not advertise a door the user cannot open
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Admin" })).toHaveCount(0);
  expect(api.requests.filter((r) => r.path.startsWith("/api/v1/admin"))).toHaveLength(0);

  await signInAs(context, baseURL!, ADMIN);
  await page.goto("/app/admin");
  await expect(page.getByRole("heading", { level: 1, name: "Admin console" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Admin" })).toBeVisible();
});

test("sources tab shows adapter health and 'Run now' queues a run", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!, ADMIN);

  await page.goto("/app/admin");
  const rows = page.getByTestId("source-row");
  await expect(rows).toHaveCount(3);

  const cppp = rows.filter({ hasText: "cppp" });
  await expect(cppp.locator("[data-status=degraded]")).toBeVisible();
  await expect(cppp).toContainText("3 records skipped");
  await expect(rows.filter({ hasText: "sam_opps" }).locator("[data-status=ok]")).toBeVisible();
  // a documented stub cannot be run from the console
  const stub = rows.filter({ hasText: "defproc" });
  await expect(stub.getByRole("button", { name: "Run now" })).toBeDisabled();

  await cppp.getByRole("button", { name: "Run now" }).click();
  await expect(page.getByText("Queued a run of cppp")).toBeVisible();
  const run = api.requests.find((r) => r.method === "POST" && r.path === "/api/v1/admin/sources/cppp/run");
  expect(run).toBeTruthy();
  expect(run!.body).toEqual({ inline: false });
  // the table re-reads after the run and the adapter is healthy again
  await expect(cppp.locator("[data-status=ok]")).toBeVisible();
});

test("run history is per source and paginated, reachable from the sources table", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!, ADMIN);

  await page.goto("/app/admin");
  await page
    .getByTestId("source-row")
    .filter({ hasText: "sam_opps" })
    .getByRole("button", { name: "History" })
    .click();

  await expect(page).toHaveURL(/tab=runs/);
  await expect(page).toHaveURL(/source=sam_opps/);
  const runs = page.getByTestId("run-row");
  await expect(runs).toHaveCount(2);
  await expect(runs.first()).toContainText("480");
  const failed = runs.filter({ hasText: "HTTP 503" });
  await expect(failed.locator("[data-status=failing]")).toBeVisible();
  await expect(page.getByText("2 total")).toBeVisible();

  await page.getByLabel("Source").selectOption("cppp");
  await expect(page.getByTestId("run-row")).toHaveCount(1);
  expect(
    api.requests.some((r) => r.path === "/api/v1/admin/sources/cppp/runs" && r.method === "GET"),
  ).toBe(true);
});

test("tenants table opens a drawer where the plan is changed and support access is granted", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!, ADMIN);

  await page.goto("/app/admin?tab=tenants");
  const rows = page.getByTestId("tenant-row");
  await expect(rows).toHaveCount(3);
  const alpha = rows.filter({ hasText: "alpha-corp" });
  await expect(alpha).toContainText("free");
  await expect(alpha).toContainText("us"); // rendered uppercase by CSS

  // the search box drives the API query
  await page.getByLabel("Search tenants").fill("beta");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByTestId("tenant-row")).toHaveCount(1);
  expect(api.requests.some((r) => r.path === "/api/v1/admin/tenants" && r.search.includes("q=beta"))).toBe(true);

  await page.getByLabel("Search tenants").fill("");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByTestId("tenant-row")).toHaveCount(3);

  await rows.filter({ hasText: "alpha-corp" }).getByRole("button", { name: "Manage" }).click();
  const drawer = page.getByTestId("tenant-drawer");
  await expect(drawer).toContainText("Alpha Corporation");
  await expect(drawer.getByTestId("drawer-cost")).toHaveText("$2.50");
  await expect(drawer).toContainText("profiles");

  // plan editor
  await drawer.locator("#tenant-plan").selectOption("pro");
  await drawer.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("alpha-corp is now on the pro plan")).toBeVisible();
  const patch = api.requests.find((r) => r.method === "PATCH" && r.path.startsWith("/api/v1/admin/tenants/"));
  expect(patch!.body).toEqual({ plan: "pro" });
  await expect(page.getByTestId("tenant-row").filter({ hasText: "alpha-corp" })).toContainText("pro");

  // support access needs a reason before it can be opened
  await drawer.getByTestId("open-support-access").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Open access" })).toBeDisabled();
  await dialog.getByLabel("Reason").fill("ticket #918: export fails");
  await dialog.getByLabel("Window").selectOption("15");
  await dialog.getByRole("button", { name: "Open access" }).click();
  await expect(page.getByText(/Support access open until/)).toBeVisible();

  const grant = api.requests.find((r) => r.method === "POST" && r.path.endsWith("/support-access"));
  expect(grant!.body).toEqual({ reason: "ticket #918: export fails", minutes: 15 });
  await expect(drawer.getByTestId("active-grant")).toContainText("ticket #918");
});

test("usage tab charts LLM cost per tenant and switches month", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!, ADMIN);

  await page.goto("/app/admin?tab=usage");
  await expect(page.getByTestId("usage-totals")).toHaveAttribute("data-period", currentPeriod());
  await expect(page.getByTestId("usage-totals")).toContainText("$3.14");
  const rows = page.getByTestId("usage-row");
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText("alpha-corp");
  await expect(rows.first().getByTestId("usage-cost")).toHaveText("$2.50");
  await expect(rows.first()).toContainText("1.5M");

  // the SVG chart has one bar per spending tenant, widest first
  const chart = page.getByTestId("cost-chart");
  await expect(chart).toBeVisible();
  await expect(chart.locator("g")).toHaveCount(2);

  // switching the month re-queries and the numbers follow
  const previous = new Date();
  previous.setUTCDate(1);
  previous.setUTCMonth(previous.getUTCMonth() - 1);
  const previousPeriod = currentPeriod(previous);
  await page.getByLabel("Month").selectOption(previousPeriod);
  await expect(page.getByTestId("usage-totals")).toHaveAttribute("data-period", previousPeriod);
  await expect(page.getByTestId("usage-totals")).toContainText("$0.83");
  await expect(page.getByTestId("usage-row").first().getByTestId("usage-cost")).toHaveText("$0.81");
  expect(
    api.requests.some(
      (r) => r.path === "/api/v1/admin/usage" && r.search.includes(`period=${previousPeriod}`),
    ),
  ).toBe(true);
});

test("health tab shows one card per check and lists unhealthy adapters", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!, ADMIN);

  await page.goto("/app/admin?tab=health");
  await expect(page.getByTestId("health-card")).toHaveCount(4);
  await expect(page.getByTestId("health-summary").locator("[data-status=degraded]")).toBeVisible();
  const storage = page.getByTestId("health-card").filter({ hasText: "Object storage" });
  await expect(storage).toContainText("local filesystem backend");
  await expect(page.getByTestId("unhealthy-adapter")).toHaveCount(2);
  await expect(page.getByTestId("unhealthy-adapter").first()).toContainText("cppp");
});
