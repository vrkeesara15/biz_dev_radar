import { expect, test, type Page } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

const VA = "9a000000-0000-4000-8000-000000000001"; // identified, watched, owned
const GRANT = "9a000000-0000-4000-8000-000000000003"; // bid_decision, no decision -> Gate 1

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

/**
 * dnd-kit's keyboard protocol: focus the card's drag handle, Space to lift,
 * an arrow key per column, Space to drop. Nothing here is a synthetic mouse
 * event — this is exactly what a keyboard user does.
 */
async function keyboardMove(page: Page, pursuitId: string, key: "ArrowRight" | "ArrowLeft", steps = 1) {
  // dnd-kit announces every step in its own live region; waiting on that is
  // how a screen-reader user knows where the card is, and how this test
  // knows the next key press will be heard.
  const live = page.locator('[role="status"][aria-live="assertive"]');
  const handle = page.locator(`[data-pursuit-id="${pursuitId}"] [data-testid="drag-handle"]`);
  await handle.focus();
  await page.keyboard.press("Space");
  await expect(live).toContainText("is over");
  for (let i = 0; i < steps; i += 1) {
    const before = await live.textContent();
    await page.keyboard.press(key);
    await expect.poll(() => live.textContent()).not.toBe(before);
  }
  await page.keyboard.press("Space");
}

test("the board renders a column per SPEC 9 stage with the API's counts", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/pipeline");
  await expect(page.getByRole("heading", { level: 1, name: "Pipeline" })).toBeVisible();

  // Seven ladder columns, in SPEC 9 order, each a labelled list.
  const columns = page.getByTestId("board").getByTestId("board-column");
  await expect(columns).toHaveCount(7);
  await expect(columns.locator("h3")).toHaveText([
    /Identified/,
    /Qualifying/,
    /Bid decision/,
    /Drafting/,
    /In review/,
    /Final approval/,
    /Submitted/,
  ]);
  await expect(page.getByRole("list", { name: "Identified pursuits" })).toBeVisible();

  // Counts come from by_stage over the whole filtered set, not the page.
  await expect(columns.nth(0).getByTestId("column-count")).toHaveText("1");
  await expect(columns.nth(2).getByTestId("column-count")).toHaveText("1");
  await expect(columns.nth(4).getByTestId("column-count")).toHaveText("0");

  // The four outcomes live behind one collapsible "Closed" section.
  const closed = page.getByTestId("closed-section");
  await expect(closed.getByRole("button", { name: /Closed/ })).toHaveAttribute("aria-expanded", "false");
  await expect(closed).toContainText("Awarded 1");
  await closed.getByRole("button", { name: /Closed/ }).click();
  await expect(closed.getByTestId("board-column")).toHaveCount(4);

  // A card carries title, buyer, value, the dual time zone due, the owner, the
  // watch flag and the SPEC 9 gate badges.
  const va = page.locator(`[data-pursuit-id="${VA}"]`);
  await expect(va).toContainText("Cloud migration and managed services");
  await expect(va).toContainText("Department of Veterans Affairs");
  await expect(va.getByTestId("card-value")).toHaveText("$2.00M – $2.50M");
  await expect(va.getByTestId("card-due")).toContainText("Dec 10, 2:00 PM EST");
  await expect(va.getByTestId("card-due")).toContainText("Dec 11, 12:30 AM IST");
  await expect(va.getByTestId("watch-flag")).toBeVisible();
  await expect(va.getByTestId("owner-avatar")).toHaveAttribute("aria-label", /Owner/);

  const grant = page.locator(`[data-pursuit-id="${GRANT}"]`);
  await expect(grant.locator('[data-gate="bid_decision"]')).toHaveText("Needs bid decision");
  await expect(grant.locator('[data-gate="matrix_recheck"]')).toHaveText("Re-check compliance");
});

test("a keyboard drag moves a card and PATCHes the new stage", async ({ page, context, baseURL }) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/pipeline");
  await expect(page.locator(`[data-pursuit-id="${VA}"]`)).toBeVisible();

  // Identified -> Qualifying, one column to the right.
  await keyboardMove(page, VA, "ArrowRight");

  await expect(page.locator(`[data-pursuit-id="${VA}"]`)).toHaveAttribute("data-stage", "qualifying");
  await expect(
    page.getByTestId("board-column").filter({ has: page.locator('[data-stage="qualifying"]') }).first(),
  ).toContainText("Cloud migration");

  const patch = api.requests.find((r) => r.method === "PATCH" && r.path === `/api/v1/pursuits/${VA}`);
  expect(patch?.body).toEqual({ stage: "qualifying" });
  expect(api.pursuits.find((row) => row.id === VA)?.stage).toBe("qualifying");

  // The column counts follow the card.
  const columns = page.getByTestId("board").getByTestId("board-column");
  await expect(columns.nth(0).getByTestId("column-count")).toHaveText("0");
  await expect(columns.nth(1).getByTestId("column-count")).toHaveText("2");
});

test("a 409 snaps the card back and shows the server's reason", async ({ page, context, baseURL }) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/pipeline");
  await expect(page.locator(`[data-pursuit-id="${GRANT}"]`)).toBeVisible();

  // Bid decision -> Drafting with no Gate 1 decision: the server refuses.
  await keyboardMove(page, GRANT, "ArrowRight");

  await expect(page.getByText("drafting requires a bid decision (Gate 1)")).toBeVisible();
  await expect(page.locator(`[data-pursuit-id="${GRANT}"]`)).toHaveAttribute("data-stage", "bid_decision");
  expect(api.pursuits.find((row) => row.id === GRANT)?.stage).toBe("bid_decision");

  const patch = api.requests.find((r) => r.method === "PATCH" && r.path === `/api/v1/pursuits/${GRANT}`);
  expect(patch?.body).toEqual({ stage: "drafting" });
});

test("the table toggle keeps the filters and both live in the URL", async ({ page, context, baseURL }) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/pipeline");

  // A filter set on the board goes straight into the query string...
  const filterBar = page.getByTestId("pipeline-filters");
  await filterBar.getByLabel("Region").selectOption("us");
  await expect(page).toHaveURL(/region=us/);
  await expect(page.getByTestId("board").getByTestId("pursuit-card")).toHaveCount(3);

  // ...and survives the switch to the table, which asks the API for it.
  await page.getByTestId("view-table").click();
  await expect(page).toHaveURL(/view=table/);
  await expect(page).toHaveURL(/region=us/);

  const table = page.getByTestId("pipeline-table");
  await expect(table).toBeVisible();
  await expect(table.locator("thead th")).toHaveText([
    "Stage",
    "Title",
    "Buyer",
    "Owner",
    "Value",
    "Due",
    "Region",
    "Last activity",
  ]);
  await expect(page.getByTestId("pursuit-row")).toHaveCount(4);
  await expect(page.getByTestId("pursuit-row").first()).toContainText("Identified");
  await expect(page.getByRole("navigation", { name: "Pagination" })).toContainText("Showing 1–4 of 4");

  const listed = api.requests.filter((r) => r.method === "GET" && r.path === "/api/v1/pursuits");
  expect(listed.at(-1)?.search).toContain("region=us");

  // A second filter narrows both the request and the table.
  await filterBar.getByLabel("Watch").selectOption("true");
  await expect(page).toHaveURL(/watch=true/);
  await expect(page.getByTestId("pursuit-row")).toHaveCount(1);
  expect(
    api.requests.filter((r) => r.method === "GET" && r.path === "/api/v1/pursuits").at(-1)?.search,
  ).toContain("watch=true");

  // Clearing puts the board back to everything.
  await filterBar.getByRole("button", { name: /^Clear/ }).click();
  await expect(page).not.toHaveURL(/region=/);
  await expect(page.getByTestId("pursuit-row")).toHaveCount(5);
});

test("the pursuit shell shows the header, key dates, tasks and comments", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ pursuits: true, members: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}`);

  const header = page.getByTestId("pursuit-header");
  await expect(header.getByTestId("pursuit-stage")).toHaveText("Identified");
  await expect(header.getByTestId("pursuit-internal-due")).toContainText("Dec 8, 2:00 PM EST");

  // Key dates with the dual time zone string, and Acknowledge on each row.
  const dates = page.getByTestId("key-dates-panel");
  await expect(dates.getByTestId("key-date-row")).toHaveCount(2);
  await expect(dates).toContainText("Nov 18, 2:00 PM EST = Nov 19, 12:30 AM IST");
  await dates.getByRole("button", { name: "Acknowledge" }).first().click();
  await expect(dates.getByTestId("acknowledged").first()).toBeVisible();

  // Adding a date posts to the M6-02 route and the row appears.
  await dates.getByRole("button", { name: "Add date" }).click();
  const dialog = page.getByTestId("key-date-dialog");
  await dialog.getByLabel("When").fill("2026-11-25T10:00");
  await dialog.getByLabel("Label").fill("Site visit");
  await dialog.getByRole("button", { name: "Add date" }).click();
  await expect(dates.getByTestId("key-date-row")).toHaveCount(3);
  expect(api.requests.some((r) => r.method === "POST" && r.path === `/api/v1/pursuits/${VA}/dates`)).toBe(true);

  // Tasks: the seeded one plus a new one, and completing it PATCHes.
  const tasks = page.getByTestId("tasks-panel");
  await expect(tasks.getByTestId("task-row")).toHaveCount(1);
  await tasks.getByLabel("New task").fill("Draft the past-performance section");
  await tasks.getByRole("button", { name: "Add task" }).click();
  await expect(tasks.getByTestId("task-row")).toHaveCount(2);
  await tasks.getByTestId("task-row").first().getByRole("checkbox").click();
  await expect.poll(() => api.pursuitTasks[VA][0].status).toBe("done");
  await expect(tasks.getByTestId("task-row").first().getByRole("checkbox")).toBeChecked();

  // Comments: the seeded thread plus a new post.
  const comments = page.getByTestId("comments-thread");
  await expect(comments.getByTestId("comment-row")).toHaveCount(1);
  await comments.getByLabel("Add a comment").fill("Pricing sheet is ready for review.");
  await comments.getByRole("button", { name: "Post comment" }).click();
  await expect(comments.getByTestId("comment-row")).toHaveCount(2);
  await expect(comments).toContainText("Pricing sheet is ready for review.");
});
