import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

const VA = "9a000000-0000-4000-8000-000000000001";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("the calendar shows every key date on its day, in both time zones", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  // November 2026 holds three of the fixture's four key dates.
  await page.goto("/app/calendar?date=2026-11-01");
  await expect(page.getByRole("heading", { level: 1, name: "Calendar" })).toBeVisible();
  await expect(page.getByTestId("calendar-title")).toHaveText("November 2026");

  const events = page.getByTestId("calendar-event");
  await expect(events).toHaveCount(2);

  // Questions due is 2 PM EST on Nov 18, which is half past midnight on the
  // 19th for this Kolkata reader — the grid puts it on the reader's day and
  // prints both clocks (SPEC 9).
  const questions = events.filter({ hasText: "Questions due" });
  await expect(questions).toContainText("Nov 18, 2:00 PM EST");
  await expect(questions).toContainText("Nov 19, 12:30 AM IST");
  await expect(page.locator('[data-date="2026-11-19"]').getByTestId("calendar-event")).toHaveCount(1);
  await expect(page.locator('[data-date="2026-11-18"]').getByTestId("calendar-event")).toHaveCount(0);

  // An Indian date needs no second clock and sits on its own day.
  await expect(page.locator('[data-date="2026-11-15"]')).toContainText("EMD / bank guarantee ready");

  // Clicking an event opens the pursuit.
  await expect(questions.first()).toHaveAttribute("href", `/app/pursuits/${VA}`);

  // Paging forward reaches December's portal submission (Dec 10 EST = Dec 11 IST).
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(page.getByTestId("calendar-title")).toHaveText("December 2026");
  await expect(page.locator('[data-date="2026-12-11"]')).toContainText("Portal submission due");
});

test("the week view shows one Sunday-to-Saturday row of the same dates", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/calendar?mode=week&date=2026-11-18");
  await expect(page.getByTestId("calendar-title")).toHaveText("Nov 15 – 21, 2026");
  await expect(page.getByTestId("calendar-day")).toHaveCount(7);
  await expect(page.getByTestId("calendar-grid")).toHaveAttribute("data-mode", "week");
  await expect(page.locator('[data-date="2026-11-19"]')).toContainText("Questions due");
  // The December date is outside this week.
  await expect(page.getByTestId("calendar-event")).toHaveCount(2);

  await page.getByRole("button", { name: "Next week" }).click();
  await expect(page.getByTestId("calendar-title")).toHaveText("Nov 22 – 28, 2026");
});

test("the subscribe panel shows the feed URL, rotates it and disconnects a calendar", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/calendar");

  const panel = page.getByTestId("subscribe-panel");
  await expect(panel.getByTestId("feed-url")).toHaveValue(/calendar\.ics\?token=/);

  // The connected Google calendar is listed and can be disconnected.
  await expect(panel.getByTestId("calendar-connections").getByRole("listitem")).toHaveCount(1);
  await expect(panel).toContainText("Google Calendar");
  await panel.getByRole("button", { name: "Disconnect Google Calendar" }).click();
  await expect(panel).toContainText("No calendar is connected");
  expect(
    api.requests.some((r) => r.method === "DELETE" && r.path.startsWith("/api/v1/me/calendar-connections/")),
  ).toBe(true);

  // Rotating issues a new URL: the token in the box changes.
  await panel.getByRole("button", { name: "Rotate token" }).click();
  await expect(panel.getByTestId("feed-url")).toHaveValue(/token=rotated-e2e-token/);
  expect(api.requests.some((r) => r.method === "POST" && r.path === "/api/v1/me/calendar-token")).toBe(true);
});
