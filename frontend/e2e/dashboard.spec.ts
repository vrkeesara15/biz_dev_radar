import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

const CLOUD_ID = "7c1d2e3f-0000-4000-8000-000000000001";
const CLOUD_PURSUIT = "9a000000-0000-4000-8000-000000000001";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("home shows high-fit matches, this week's dates, the KPI row and the alerts inbox", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ matches: true, dashboard: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app");
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();

  // High-fit today: the API is asked for the scored, still-biddable notices only.
  // Polled, not read once: the card fetches after the heading paints, and under
  // a loaded dev server that read can land after this line (M7-13).
  await expect
    .poll(() =>
      api.requests.find((r) => r.method === "GET" && r.path === "/api/v1/opportunities")?.search,
    )
    .toContain("min_score=70");
  const search = api.requests.find((r) => r.method === "GET" && r.path === "/api/v1/opportunities");
  expect(search?.search).toContain("status=open%2Cclosing_soon");
  const highFit = page.getByTestId("high-fit-card");
  await expect(highFit.getByTestId("high-fit-list").getByRole("listitem")).toHaveCount(2);
  await expect(highFit).toContainText("Cloud migration and managed services");
  await expect(highFit).toContainText("82");
  // The 41-scoring grant is below the band and never reaches the card.
  await expect(highFit).not.toContainText("Community economic development");
  await expect(highFit.getByRole("link", { name: "High-fit today" })).toHaveAttribute(
    "href",
    "/app/opportunities?status=open%2Cclosing_soon&min_score=70",
  );

  // Due this week comes from GET /dashboard: pursuit deep links, the server's
  // countdown and the dual time zone string (SPEC 9).
  const dueWeek = page.getByTestId("due-week-card");
  await expect(dueWeek.getByTestId("due-week-list").getByRole("listitem")).toHaveCount(2);
  await expect(dueWeek.getByRole("link", { name: /Cloud migration/ })).toHaveAttribute(
    "href",
    `/app/pursuits/${CLOUD_PURSUIT}`,
  );
  await expect(dueWeek.getByTestId("due-countdown").first()).toHaveText("4d 9h");
  await expect(dueWeek).toContainText("Oct 1, 2:00 PM EDT");
  await expect(dueWeek).toContainText("11:30 PM IST");

  // Pipeline value: USD and INR, the latter in crore (SPEC 9 / lib/money).
  const pipeline = page.getByTestId("pipeline-value-card");
  await expect(pipeline.getByTestId("pipeline-total")).toContainText("$4.00M");
  await expect(pipeline.getByTestId("pipeline-total")).toContainText("₹33.20 Cr");
  await expect(pipeline.getByTestId("pipeline-by-stage")).toContainText("Identified");
  // Stages come back out of order and are rendered in SPEC 9's board order.
  await expect(pipeline.getByTestId("open-by-stage")).toHaveText(
    "Identified 5 · Qualifying 3 · Drafting 2",
  );

  // The three SPEC 9 KPIs, each with what it is based on.
  await expect(pipeline.getByTestId("kpi-win-rate")).toHaveText("32%");
  await expect(pipeline.getByTestId("kpi-alert-precision")).toHaveText("74%");
  await expect(pipeline.getByTestId("kpi-hours-saved")).toHaveText("500");
  await expect(pipeline.getByTestId("kpi-hours-basis")).toContainText("an estimate, not a measurement");

  // Alerts inbox: the two unread notifications, with mark-read removing one.
  const inbox = page.getByTestId("alerts-inbox-card");
  await expect(inbox.getByTestId("alerts-inbox-list").getByRole("listitem")).toHaveCount(2);
  await expect(
    inbox.getByRole("link", { name: "Cloud migration and managed services for VA regional offices" }),
  ).toHaveAttribute("href", `/app/opportunities/${CLOUD_ID}`);
  await inbox.getByRole("button", { name: /Mark “Deadline moved/ }).click();
  await expect(inbox.getByTestId("alerts-inbox-list").getByRole("listitem")).toHaveCount(1);
  expect(api.notifications.find((n) => n.id === "11111111-0000-4000-8000-000000000002")?.read_at).not.toBeNull();
});

test("a rate with an empty denominator reads as 'not yet', never as zero", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ dashboard: true });
  api.dashboard = {
    ...(api.dashboard as Record<string, unknown>),
    win_rate: null,
    awarded: 0,
    lost: 0,
    alert_precision: null,
    alert_feedback_rated: 0,
  };
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app");
  const pipeline = page.getByTestId("pipeline-value-card");
  await expect(pipeline.getByTestId("kpi-win-rate")).toHaveText("Not enough decisions yet");
  await expect(pipeline.getByTestId("kpi-alert-precision")).toHaveText("Collecting feedback");
  await expect(pipeline).not.toContainText("0%");
});

test("home degrades to empty states when the dashboard read fails and nothing is scored", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  api.notifications = [];
  await api.install(page);
  // Registered after install, so it wins: the aggregate is unreadable.
  await page.route("**/api/v1/dashboard", (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: "{}" }),
  );
  await signInAs(context, baseURL!);

  await page.goto("/app");
  await expect(page.getByTestId("due-week-card")).toContainText("The dashboard could not be read (500)");
  await expect(page.getByTestId("pipeline-value-card")).toContainText(
    "The dashboard could not be read (500)",
  );
  // Nothing is scored yet, so the API (and the mock) still answer the whole page
  // and the card shows the rows with an em dash instead of a score.
  await expect(page.getByTestId("high-fit-card").getByLabel("Not scored yet").first()).toBeVisible();
  await expect(page.getByTestId("alerts-inbox-card")).toContainText("Inbox zero");
  await expect(page.getByTestId("notification-unread-count")).toHaveCount(0);
});
