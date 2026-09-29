/**
 * The SPEC 12 accessibility gate: every page a Playwright flow visits is run
 * through axe-core and must carry no `serious` or `critical` violation.
 *
 * Why those two impacts: axe's `minor`/`moderate` findings are advisory (a
 * redundant landmark, a heading level skipped inside a card), while `serious`
 * and `critical` are the ones that stop a keyboard or screen-reader user —
 * unlabelled controls, contrast below 4.5:1, a control with no accessible
 * name, a role used without its required children. A real violation is fixed
 * in the component; `exclude` and `disableRules` exist for third-party widget
 * internals we do not own, each use carrying a comment naming the widget, and
 * are not used anywhere in this suite today (OQ-152).
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** WCAG 2.1 A and AA — what SPEC 12 "axe accessibility check" is measured against. */
export const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] as const;

export const BLOCKING_IMPACTS = ["serious", "critical"] as const;

/**
 * Rules whose finding can be an artefact of a client-side navigation still in
 * flight rather than a defect. The rule is NOT disabled: a finding is retried
 * on a settled page, and it fails the test if it is still there.
 */
const TRANSIENT_RULES = new Set(["document-title"]);

export type AxeOptions = {
  /** CSS selectors to leave out. Third-party widget internals only, with a reason. */
  exclude?: string[];
  /** Rule ids to disable. Same rule: a reason per entry, never to hide our own bug. */
  disableRules?: string[];
};

/**
 * Wait until the document has carried a non-empty <title> for three checks in
 * a row. Next's App Router resolves route metadata after the client navigation
 * commits, and in dev it takes the <title> out and puts it back, so a single
 * "is it set?" probe can pass and the element be gone a frame later.
 */
async function settleTitle(page: Page): Promise<void> {
  const deadline = Date.now() + 15_000;
  let stable = 0;
  while (Date.now() < deadline && stable < 3) {
    const present = await page
      .evaluate(() => {
        const title = document.querySelector("title");
        return !!title && (title.textContent ?? "").trim().length > 0;
      })
      .catch(() => false);
    stable = present ? stable + 1 : 0;
    if (stable < 3) await page.waitForTimeout(120);
  }
}

/** Freeze animations and wait for toasts, then run axe once. */
async function analyseSettled(page: Page, options: AxeOptions) {
  await settleTitle(page);

  // Settle CSS transitions before measuring. Several components animate their
  // colours (a badge that flips from `outline` to `default` when a count is
  // met carries `transition-all`), and axe sampling a page mid-transition
  // reads a blended colour and reports a contrast failure no user ever sees.
  // This suppresses the ANIMATION, not a rule or an element: every node is
  // still analysed, at its settled colour.
  const freeze = await page.addStyleTag({
    content:
      "*,*::before,*::after{transition-duration:0s !important;transition-delay:0s !important;" +
      "animation-duration:0s !important;animation-delay:0s !important;}",
  });
  await page.waitForTimeout(50);

  // Sonner renders a toast at a partial opacity while it slides in and while
  // it stacks behind another. Wait for every toast to be fully shown (1) or
  // fully hidden (0) so the toast IS measured, at the colour it is read at.
  await page
    .waitForFunction(
      () =>
        Array.from(document.querySelectorAll("[data-sonner-toast]")).every((toast) => {
          const opacity = window.getComputedStyle(toast).opacity;
          return opacity === "1" || opacity === "0";
        }),
      undefined,
      { timeout: 5_000 },
    )
    .catch(() => undefined);

  let builder = new AxeBuilder({ page }).withTags([...AXE_TAGS]);
  for (const selector of options.exclude ?? []) builder = builder.exclude(selector);
  if (options.disableRules?.length) builder = builder.disableRules(options.disableRules);

  const results = await builder.analyze();
  await freeze.evaluate((node: Element) => node.remove()).catch(() => undefined);
  return results.violations.filter(
    (violation) =>
      violation.impact && (BLOCKING_IMPACTS as readonly string[]).includes(violation.impact),
  );
}

/**
 * Analyse the page as it currently stands and fail with a readable list.
 * `label` names the screen so a CI failure says which one broke.
 */
export async function expectNoA11yViolations(page: Page, label: string, options: AxeOptions = {}) {
  let blocking = await analyseSettled(page, options);
  // A finding that is ONLY a transient rule is re-measured on a settled page,
  // twice, before it is believed. A real one survives all three passes.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (blocking.length === 0) break;
    if (!blocking.every((violation) => TRANSIENT_RULES.has(violation.id))) break;
    await page.waitForTimeout(500);
    blocking = await analyseSettled(page, options);
  }

  const readable = blocking.map(
    (violation) =>
      `${violation.id} (${violation.impact}, ${violation.nodes.length} node(s)): ${violation.help}\n` +
      violation.nodes
        .slice(0, 4)
        .map((node) => `      ${node.target.join(" ")}\n      ${node.failureSummary ?? ""}`)
        .join("\n"),
  );
  expect(readable, `axe serious/critical violations on ${label}`).toEqual([]);
}
