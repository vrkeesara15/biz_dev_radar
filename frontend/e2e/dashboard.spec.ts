import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

const CLOUD_ID = "7c1d2e3f-0000-4000-8000-000000000001";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("home shows high-fit matches, this week's dates, pipeline value and the alerts inbox", async ({
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
  const search = api.requests.find((r) => r.method === "GET" && r.path === "/api/v1/opportunities");
  expect(search?.search).toContain("min_score=70");
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

  // Due this week comes from GET /dashboard.
  const dueWeek = page.getByTestId("due-week-card");
  await expect(dueWeek.getByTestId("due-week-list").getByRole("listitem")).toHaveCount(2);
  await expect(dueWeek.getByRole("link", { name: /Questions due/ })).toHaveAttribute(
    "href",
    `/app/opportunities/${CLOUD_ID}`,
  );

  // Pipeline value: USD and INR, the latter in crore (SPEC 9 / lib/money).
  const pipeline = page.getByTestId("pipeline-value-card");
  await expect(pipeline.getByTestId("pipeline-total")).toContainText("$4.00M");
  await expect(pipeline.getByTestId("pipeline-total")).toContainText("₹33.20 Cr");
  await expect(pipeline.getByTestId("pipeline-by-stage")).toContainText("Identified");
  await expect(pipeline).toContainText("Identified 5 · Reviewing 3 · Drafting 2 · Submitted 1");
  await expect(pipeline).toContainText("32%");
  await expect(pipeline).toContainText("74%");

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

test("home degrades to empty states when the dashboard route and the matches are not there yet", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  api.notifications = [];
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app");
  await expect(page.getByTestId("due-week-card")).toContainText(
    "Pipeline metrics arrive with the pursuits milestone",
  );
  await expect(page.getByTestId("pipeline-value-card")).toContainText(
    "Pipeline metrics arrive with the pursuits milestone",
  );
  // Nothing is scored yet, so the API (and the mock) still answer the whole page
  // and the card shows the rows with an em dash instead of a score.
  await expect(page.getByTestId("high-fit-card").getByLabel("Not scored yet").first()).toBeVisible();
  await expect(page.getByTestId("alerts-inbox-card")).toContainText("Inbox zero");
  await expect(page.getByTestId("notification-unread-count")).toHaveCount(0);
});
