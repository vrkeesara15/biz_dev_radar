import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

const CLOUD_ID = "7c1d2e3f-0000-4000-8000-000000000001";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("the bell counts unread notifications, marks them read and takes a one-click action", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app");
  const bell = page.getByTestId("notification-bell");
  await expect(page.getByTestId("notification-unread-count")).toHaveText("2");
  await expect(bell).toHaveAttribute("aria-label", "Notifications, 2 unread");

  await bell.click();
  const panel = page.getByTestId("notification-panel");
  await expect(panel.getByTestId("notification-item")).toHaveCount(3);
  await expect(
    panel.getByRole("link", { name: "Cloud migration and managed services for VA regional offices" }),
  ).toHaveAttribute("href", `/app/opportunities/${CLOUD_ID}`);

  // One-click actions come from the payload, rewritten onto this origin.
  await panel.getByRole("button", { name: /^Pursue/ }).click();
  await expect(page.getByText("Pursue recorded")).toBeVisible();
  expect(api.actionsTaken).toContainEqual({ action: "pursue", token: "tok-pursue", reason: null });

  // Pass asks for a reason first (SPEC 7: "Pass (with reason)").
  await panel.getByRole("button", { name: /^Pass/ }).click();
  await panel.getByLabel("Why pass?").fill("No capacity this quarter");
  await panel.getByRole("button", { name: "Record pass" }).click();
  await expect.poll(() => api.actionsTaken.find((a) => a.action === "pass")?.reason).toBe(
    "No capacity this quarter",
  );

  // Marking the remaining unread item read clears the badge.
  await panel.getByRole("button", { name: /Mark “Deadline moved/ }).click();
  await expect(page.getByTestId("notification-unread-count")).toHaveCount(0);
  expect(api.notifications.every((row) => row.read_at !== null)).toBe(true);
});

test("mark all read clears every unread notification", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app");
  await page.getByTestId("notification-bell").click();
  await page.getByTestId("mark-all-read").click();
  await expect(page.getByText("2 notifications marked read")).toBeVisible();
  await expect(page.getByTestId("notification-unread-count")).toHaveCount(0);
  await expect(page.getByTestId("mark-all-read")).toBeDisabled();
});

test("notification preferences edit the matrix, quiet hours, zone, digest time and thresholds", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  api.prefs = { ...api.prefs, unsubscribed_categories: ["digest"] };
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings/notifications");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
  await expect(page.getByTestId("settings-tab-notifications")).toHaveAttribute("aria-current", "page");

  // Matrix: 8 events x 5 channels, email on by default.
  const matrix = page.getByTestId("channel-matrix");
  await expect(matrix.getByRole("row")).toHaveCount(9);
  const slackHighFit = matrix.getByLabel("Slack for New High-fit match");
  await expect(matrix.getByLabel("Email for New High-fit match")).toBeChecked();
  await expect(slackHighFit).not.toBeChecked();
  await slackHighFit.check();

  // A whole column at once.
  await matrix.getByRole("button", { name: "Select Web push for every event" }).click();
  await expect(matrix.getByLabel("Web push for Daily digest")).toBeChecked();

  // Quiet hours, the time zone (searchable) and the digest time.
  await page.getByLabel("Start", { exact: true }).fill("21:00");
  await page.getByLabel("End", { exact: true }).fill("07:00");
  await page.getByLabel("Search time zones").fill("kolkata");
  await page.getByLabel("Time zone", { exact: true }).selectOption("Asia/Kolkata");
  await page.getByLabel("Digest time").fill("07:30");

  // The two thresholds, with the backend's ordering rule enforced in the form.
  await page.getByLabel("Minimum score for the digest").fill("90");
  await expect(page.getByTestId("min-score-error")).toContainText("cannot exceed");
  await expect(page.getByRole("button", { name: "Save preferences" })).toBeDisabled();
  await page.getByLabel("Minimum score for the digest").fill("55");
  await page.getByLabel("Minimum score for an instant alert").fill("75");

  // Email opt-outs are shown read-only, from the API.
  await expect(page.getByTestId("unsubscribed-categories")).toContainText("Daily digest");

  await page.getByRole("button", { name: "Save preferences" }).click();
  await expect(page.getByText("Notification preferences saved")).toBeVisible();

  const put = api.requests.filter((r) => r.method === "PUT" && r.path === "/api/v1/me/notification-prefs").at(-1);
  expect(put?.body).toMatchObject({
    quiet_hours_start: "21:00",
    quiet_hours_end: "07:00",
    tz: "Asia/Kolkata",
    digest_time: "07:30",
    min_score_instant: 75,
    min_score_digest: 55,
  });
  const channels = (put?.body as { channels_by_event: Record<string, string[]> }).channels_by_event;
  expect(channels.high_fit_match).toEqual(["email", "slack", "web_push"]);
  expect(channels.digest).toEqual(["email", "web_push"]);
});

test("web push explains itself when the browser or the deployment cannot do it", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings/notifications");
  const push = page.getByTestId("push-opt-in");
  // Chromium supports push, but the e2e server has no VAPID key configured.
  await expect(push).toHaveAttribute("data-state", "unconfigured");
  await expect(push).toContainText("Web push is not configured for this deployment.");
  await expect(push.getByRole("button", { name: "Enable web push" })).toBeDisabled();
});

test("saved searches: create from the search page, then rename and tune the alert rule", async ({
  page,
  context,
  baseURL,
}) => {
  const api = new MockApi({ savedSearches: true, alertRules: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  // Create from the search page.
  await page.goto("/app/opportunities?region=us&type=rfp");
  await page.getByTestId("saved-search-bar").getByRole("button", { name: "Save this search" }).click();
  const dialog = page.getByRole("dialog", { name: "Save this search" });
  await dialog.getByLabel("Name").fill("US RFPs");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved “US RFPs”")).toBeVisible();
  expect(api.savedSearches).toHaveLength(1);
  expect(api.savedSearches[0]).toMatchObject({ name: "US RFPs", filters: { region: "us", type: "rfp" } });

  // Manage it in settings.
  await page.goto("/app/settings/saved-searches");
  const row = page.getByTestId("saved-search-row");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("United States · RFP");

  // The alert rule: instant instead of digest, Slack alongside email, enabled.
  await row.getByLabel("Alert mode").selectOption("instant");
  await expect.poll(() => api.alertRules.length).toBe(1);
  expect(api.alertRules[0]).toMatchObject({
    saved_search_id: api.savedSearches[0].id,
    mode: "instant",
    channels: ["email"],
    enabled: true,
  });
  await expect(row.getByLabel("Slack")).toBeEnabled();
  await row.getByLabel("Slack").click();
  await expect.poll(() => (api.alertRules[0].channels as string[]).join(",")).toBe("email,slack");
  await expect(row.getByLabel("Slack")).toBeChecked();
  await expect(row.getByLabel("Minimum score")).toBeEnabled();
  await row.getByLabel("Minimum score").fill("60");
  await row.getByLabel("Minimum score").blur();
  await expect.poll(() => api.alertRules[0].min_score).toBe(60);

  // Rename and delete go to /saved-searches/{id}.
  await row.getByRole("button", { name: "Rename US RFPs" }).click();
  const renameDialog = page.getByRole("dialog", { name: "Rename saved search" });
  await renameDialog.getByLabel("Name").fill("US RFPs and RFQs");
  await renameDialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("saved-search-row")).toContainText("US RFPs and RFQs");
  await page.getByRole("button", { name: "Delete US RFPs and RFQs" }).click();
  await expect(page.getByTestId("saved-search-row")).toHaveCount(0);
  expect(api.savedSearches).toHaveLength(0);
});

test("saved searches say so when the API is not deployed yet", async ({ page, context, baseURL }) => {
  const api = new MockApi();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto("/app/settings/saved-searches");
  await expect(page.getByTestId("saved-searches-unavailable")).toContainText(
    "Saved searches arrive with the matching milestone",
  );
});

test("thumbs down asks why, and the reason reaches the feedback route", async ({ page, context, baseURL }) => {
  const api = new MockApi({ matches: true, feedback: true });
  await api.install(page);
  await signInAs(context, baseURL!);

  // From a search row.
  await page.goto("/app/opportunities");
  const row = page.getByTestId("result-row").filter({ hasText: "Cloud migration" });
  await row.getByRole("button", { name: /^Not relevant/ }).click();
  const dialog = page.getByTestId("not-relevant-dialog");
  await dialog.getByLabel("Reason").selectOption("wrong_geography");
  await dialog.getByLabel("Anything to add? (optional)").fill("We only bid in the north-east");
  await dialog.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByText("Thanks — we will show fewer of these")).toBeVisible();
  expect(api.feedback.at(-1)).toMatchObject({
    opportunity_id: CLOUD_ID,
    thumb: "down",
    reason: "Wrong geography — We only bid in the north-east",
  });

  // And from the fit-score card on the detail page.
  await page.goto(`/app/opportunities/${CLOUD_ID}`);
  await page.getByTestId("fit-score-card").getByRole("button", { name: /^Relevant/ }).click();
  await expect(page.getByText("Thanks — more like this")).toBeVisible();
  expect(api.feedback.at(-1)).toMatchObject({ thumb: "up", reason: null });
});
