import { expect, test } from "@playwright/test";

import { signInAs } from "./auth";
import { MockApi } from "./mock-api";

/** The fixture's pursuit: identified, Gate 1 open, two agent-written sections. */
const VA = "9a000000-0000-4000-8000-000000000001";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

const workspace = () => new MockApi({ pursuits: true, members: true, workspace: true });

test("the workspace renders the SPEC 10.4 tabs, the cost meter and the run state", async ({
  page,
  context,
  baseURL,
}) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}`);

  // Header: title, buyer, stage, dual-zone internal deadline.
  await expect(page.getByTestId("workspace-title")).toHaveText(
    "Cloud migration and managed services for VA regional offices",
  );
  await expect(page.getByTestId("workspace-header")).toContainText("Department of Veterans Affairs");
  await expect(page.getByTestId("pursuit-stage")).toHaveText("Identified");
  await expect(page.getByTestId("pursuit-internal-due")).toContainText("Dec 8, 2:00 PM EST");

  // Cost meter: cost_so_far vs cap, plus the month's remaining budget.
  await expect(page.getByTestId("cost-values")).toHaveText("$12.40 of $15.00");
  await expect(page.getByTestId("cost-meter")).toContainText("$151.90 left");
  await expect(page.getByTestId("cost-meter").getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "83",
  );

  // Run status and the gate it is waiting at.
  await expect(page.getByTestId("run-status")).toHaveText("Paused");
  await expect(page.getByTestId("run-gate")).toHaveText("Gate 1: bid/no-bid decision");

  // Seven tabs, in SPEC 10.4 order, as one roving-tabindex tab list.
  const tabs = page.getByRole("tab");
  await expect(tabs).toHaveText([
    /Bid\/no-bid/,
    /Compliance matrix/,
    /Drafts/,
    /Pricing/,
    /Checklist/,
    /Tasks/,
    /Activity/,
  ]);
  await expect(tabs.first()).toHaveAttribute("aria-selected", "true");
  await expect(tabs.first()).toHaveAttribute("tabindex", "0");
  await expect(tabs.nth(1)).toHaveAttribute("tabindex", "-1");

  // Arrow keys move the selection, Home returns to the first tab.
  await tabs.first().focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("panel-matrix")).toBeVisible();
  await expect(tabs.nth(1)).toBeFocused();
  await page.keyboard.press("Home");
  await expect(page.getByTestId("panel-bid-no-bid")).toBeVisible();
});

test("the bid/no-bid tab shows the scorecard and records Gate 1", async ({ page, context, baseURL }) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}`);

  const scorecard = page.getByTestId("scorecard");
  await expect(scorecard.getByTestId("scorecard-criterion")).toHaveCount(6);
  await expect(scorecard.locator('[data-criterion="win_probability"]')).toContainText("38");
  await expect(scorecard.getByTestId("scorecard-recommendation")).toContainText("bid");
  await expect(scorecard.getByTestId("scorecard-gap")).toContainText("FedRAMP High");
  await expect(scorecard.getByTestId("scorecard-teaming")).toContainText("FedRAMP High cloud prime");
  await expect(scorecard.getByTestId("scorecard-reason")).toHaveCount(2);

  // Gate 1: a note and the two buttons; the record replaces them once decided.
  await page.getByTestId("gate-1").getByLabel("Note").fill("Fit is strong and the window is long enough.");
  await page.getByTestId("decide-bid").click();

  await expect(page.getByTestId("decision-record")).toContainText("bid by E2E Owner");
  await expect(page.getByTestId("decision-record")).toContainText("Fit is strong");
  await expect(page.getByTestId("decide-bid")).toBeDisabled();
  await expect(page.getByTestId("decide-no-bid")).toBeDisabled();

  const decision = api.requests.find((row) => row.path === `/api/v1/pursuits/${VA}/decision`);
  expect(decision?.method).toBe("POST");
  expect(decision?.body).toMatchObject({ decision: "bid" });
  await expect(page.getByTestId("pursuit-stage")).toHaveText("Drafting");
});

test("the compliance matrix lists requirements with clickable page citations", async ({
  page,
  context,
  baseURL,
}) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}?tab=matrix`);

  const rows = page.getByTestId("matrix-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("R-001");
  await expect(rows.first()).toContainText("Technical approach");
  await expect(rows.first()).toContainText("drafted");

  // The citation opens the source document at its page.
  const citation = rows.first().getByTestId("matrix-citation");
  await expect(citation).toHaveText("page 12");
  await expect(citation).toHaveAttribute("href", /RFP-36C24825R0042|download#page=12/);

  await expect(page.getByTestId("format-rules")).toContainText("Times New Roman");
  await expect(page.getByTestId("format-rules")).toContainText("30");
});

test("the drafts tab highlights unsupported claims, lists citations and saves with base_version", async ({
  page,
  context,
  baseURL,
}) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}?tab=drafts`);

  // Left: one row per section with its flag counts.
  await expect(page.getByTestId("section-row")).toHaveCount(2);
  await expect(page.getByTestId("section-unsupported").first()).toHaveText("1 unsupported");

  // Centre: the AI-draft label, its disclaimer, and the red decoration.
  await expect(page.getByTestId("ai-draft-badge")).toBeVisible();
  await expect(page.getByTestId("ai-disclaimer")).toContainText(
    "Verify every detail on the official portal before submitting.",
  );
  const unsupported = page.locator("[data-unsupported]");
  await expect(unsupported).toHaveCount(1);
  await expect(unsupported).toHaveText("Our team holds an ISO 27001 certificate.");
  await expect(unsupported).toHaveAttribute("title", /Unsupported claim \(certification\)/);

  // The [NEEDS INPUT] marker is marked in the body and reachable as a chip.
  await expect(page.locator("[data-needs-input]")).toHaveText("[NEEDS INPUT: site lead name]");
  const chip = page.getByTestId("needs-input-chip");
  await expect(chip).toContainText("site lead name");

  // Right: the citations panel and the red-team findings.
  const citations = page.getByTestId("citations-panel");
  await expect(citations.getByTestId("citation-row")).toHaveCount(1);
  await expect(citations).toContainText("past performance");
  await expect(citations).toContainText("page 3");
  await expect(citations).toContainText("Migrated 14 agency workloads");
  await expect(page.getByTestId("red-team-issue")).toContainText("Section L.4 asks for three");

  // The comment thread is anchored to this draft section.
  const comments = page.getByTestId("comments-thread");
  // The pursuit-level thread is not this section's: it starts empty.
  await expect(comments.getByTestId("comment-row")).toHaveCount(0);
  await comments.getByLabel("Add a comment").fill("Add the third reference here.");
  await comments.getByRole("button", { name: "Post comment" }).click();
  await expect(comments.getByTestId("comment-row")).toHaveCount(1);
  await expect(comments.getByTestId("comment-row")).toContainText("Add the third reference here.");
  const posted = api.requests.find(
    (row) => row.method === "POST" && row.path === `/api/v1/pursuits/${VA}/comments`,
  );
  expect(posted?.body).toMatchObject({
    target_type: "draft_section",
    target_id: "d1000000-0000-4000-8000-000000000001",
  });

  // Editing then saving sends the version the editor loaded.
  await expect(page.getByTestId("base-version")).toContainText("Version 2");
  await page.getByTestId("draft-body").click();
  await page.keyboard.type(" Confirmed.");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("base-version")).toContainText("Version 3");

  const put = api.requests.find(
    (row) => row.method === "PUT" && row.path === `/api/v1/pursuits/${VA}/drafts/past-performance`,
  );
  expect(put?.body).toMatchObject({ base_version: 2 });
  expect(String((put?.body as { body_html: string }).body_html)).toContain("Confirmed.");
});

test("a save against a stale version offers the conflict, the reload and the comparison", async ({
  page,
  context,
  baseURL,
}) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}?tab=drafts`);
  await expect(page.getByTestId("base-version")).toContainText("Version 2");

  // Someone else saves version 3 while this editor holds 2.
  const draft = api.drafts["past-performance"];
  const current = draft.current as Record<string, unknown>;
  current.version = 3;
  current.body_html = "<p>Rewritten by the other reviewer.</p>";
  draft.versions = [1, 2, 3];

  await page.getByTestId("draft-body").click();
  await page.keyboard.type(" My edit.");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  const conflict = page.getByTestId("save-conflict");
  await expect(conflict).toContainText("is at version 3, not 2");
  await expect(conflict).toContainText("reload the section and reapply your edit");
  await expect(conflict.getByTestId("conflict-diff")).toBeVisible();

  await conflict.getByTestId("conflict-reload").click();
  await expect(page.getByTestId("base-version")).toContainText("Version 3");
  await expect(page.getByTestId("draft-body")).toContainText("Rewritten by the other reviewer.");
  await expect(page.getByTestId("save-conflict")).toHaveCount(0);
});

test("a [NEEDS INPUT] chip opens the task the drafter made for it", async ({ page, context, baseURL }) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}?tab=drafts`);
  await page.getByTestId("needs-input-chip").click();

  await expect(page.getByTestId("panel-tasks")).toBeVisible();
  await expect(page.getByTestId("linked-task")).toContainText("Who is the named site lead");
  await expect(page.getByTestId("tasks-panel")).toBeVisible();
  await expect(page.getByTestId("key-dates-panel")).toBeVisible();
});

test("Gate 2 refuses before the red team, then approves, marks final and exports", async ({
  page,
  context,
  baseURL,
}) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}`);

  // Approving before agent 8 has run surfaces the server's reason.
  await page.getByTestId("approve-package").click();
  await expect(page.getByTestId("gate-2-error")).toContainText("the red-team reviewer has not run yet");
  await expect(page.getByTestId("mark-final")).toBeDisabled();

  // Run the red team from the header's step control, then approve.
  await page.getByTestId("agent-step").selectOption("red_team");
  await page.getByTestId("run-step").click();
  await expect.poll(() => api.redTeamRan).toBe(true);

  await page.getByTestId("approve-package").click();
  await expect(page.getByTestId("package-approved")).toBeVisible();
  await expect(page.getByTestId("approve-package")).toBeDisabled();

  // Mark final, then export: the signed URL comes back on the row.
  await page.getByTestId("mark-final").click();
  await expect(page.getByTestId("package-final")).toBeVisible();

  const popup = context.waitForEvent("page").catch(() => null);
  await page.getByTestId("export-docx").click();
  await expect(page.getByTestId("export-row")).toHaveCount(1);
  const row = page.getByTestId("export-row").first();
  await expect(row).toContainText("36C24825R0042_package.docx");
  await expect(row).toContainText("python-docx");
  await expect(row).toContainText("final");
  await expect(row.getByRole("link")).toHaveAttribute(
    "href",
    "https://files.bidradar.test/exports/docx?signature=e2e",
  );
  await popup;

  const exportRequest = api.requests.find(
    (request) => request.method === "POST" && request.path === `/api/v1/pursuits/${VA}/export`,
  );
  expect(exportRequest?.search).toContain("format=docx");
});

test("the checklist tab shows the submission packet and never offers to submit", async ({
  page,
  context,
  baseURL,
}) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}?tab=checklist`);

  await expect(page.getByTestId("checklist-item")).toHaveCount(3);
  await expect(page.getByTestId("checklist-card")).toContainText("Active SAM.gov registration");

  const packet = page.getByTestId("packet-card");
  await expect(packet.getByTestId("packet-portal")).toHaveAttribute(
    "href",
    "https://sam.gov/opp/36C24825R0042/view",
  );
  await expect(packet.getByTestId("upload-step")).toHaveCount(2);
  await expect(packet.getByTestId("signature-step")).toContainText("SF 33");
  await expect(packet.getByTestId("sam-note")).toContainText("SAM.gov account");
  await expect(packet.getByTestId("packet-deadline")).toContainText(
    "Dec 10, 2:00 PM EST = Dec 11, 12:30 AM IST",
  );
  await expect(packet.getByTestId("packet-disclaimer")).toContainText(
    "Verify every detail on the official portal before submitting.",
  );
  await expect(page.getByRole("button", { name: /submit/i })).toHaveCount(0);
});

test("the pricing and activity tabs read what the API exposes", async ({ page, context, baseURL }) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!);

  await page.goto(`/app/pursuits/${VA}?tab=pricing`);
  await expect(page.getByTestId("pricing-summary")).toContainText("6");
  await expect(page.getByTestId("pricing-tab")).toContainText("Two labour categories are not on the rate card");
  await page.getByTestId("pricing-export-xlsx").click();
  await expect.poll(() => api.exports.length).toBe(1);
  expect(api.exports[0].format).toBe("xlsx");

  await page.goto(`/app/pursuits/${VA}?tab=activity`);
  const rows = page.getByTestId("activity-row");
  await expect(rows.filter({ hasText: "Pursuit created" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "Past performance — version 2" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "Task opened: Past performance: Who is the named site lead" })).toHaveCount(1);
  await expect(page.locator('[data-kind="comment"]')).toHaveCount(1);
});

test("a viewer sees the workspace read-only", async ({ page, context, baseURL }) => {
  const api = workspace();
  await api.install(page);
  await signInAs(context, baseURL!, { role: "viewer" });

  await page.goto(`/app/pursuits/${VA}`);

  // No Gate 1, no Gate 2, no agent run, no exports.
  await expect(page.getByTestId("gate-1-readonly")).toBeVisible();
  await expect(page.getByTestId("decide-bid")).toHaveCount(0);
  await expect(page.getByTestId("gate-2-readonly")).toBeVisible();
  await expect(page.getByTestId("approve-package")).toHaveCount(0);
  await expect(page.getByTestId("mark-final")).toHaveCount(0);
  await expect(page.getByTestId("export-buttons")).toHaveCount(0);
  await expect(page.getByTestId("run-step")).toBeDisabled();

  // And no draft text at all (SPEC 3: read-only dashboards).
  await page.getByRole("tab", { name: "Drafts" }).click();
  await expect(page.getByTestId("drafts-forbidden")).toBeVisible();
  await expect(page.getByTestId("draft-editor")).toHaveCount(0);

  // The matrix and the packet are still readable.
  await page.getByRole("tab", { name: "Compliance matrix" }).click();
  await expect(page.getByTestId("matrix-row")).toHaveCount(2);
});
